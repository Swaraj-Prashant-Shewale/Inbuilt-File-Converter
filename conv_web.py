"""Web formats: SVG (vector) and MHTML web archives, rendered via Edge headless.

Plugin module: see FAMILIES / selftest_cases at the bottom.
"""
import base64
import os
import re
import tempfile
from pathlib import Path

import engines

_MAX_DIM = 8192
_UNITS = {"": 1.0, "px": 1.0, "pt": 96 / 72, "pc": 16.0, "in": 96.0,
          "cm": 96 / 2.54, "mm": 96 / 25.4}
# Edge flag: resolve nothing but localhost, so no remote fetch can happen.
_NO_NET = "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost"


# ---------------------------------------------------------------- svg

def _parse_svg_root(data):
    """Parse SVG bytes safely (no entity expansion / external entities)."""
    if re.search(rb"<!ENTITY", data, re.I):
        raise engines.ConvertError("SVG with entity declarations is refused.")
    try:
        from defusedxml import ElementTree as ET
    except ImportError:
        import xml.etree.ElementTree as ET  # ENTITY already refused above
    try:
        return ET.fromstring(data)
    except Exception as e:
        raise engines.ConvertError(f"Invalid SVG: {e}")


def _length(value):
    """CSS length -> px, or None for missing / percentage / unparsable."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*",
                     value or "")
    if not m or m.group(2).lower() not in _UNITS:
        return None
    return float(m.group(1)) * _UNITS[m.group(2).lower()]


def _svg_size(data):
    """Intrinsic (width, height) in px from width/height/viewBox."""
    root = _parse_svg_root(data)
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise engines.ConvertError("Not an SVG image.")
    w, h = _length(root.get("width")), _length(root.get("height"))
    vb = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    vbw = vbh = None
    if len(vb) == 4:
        try:
            vbw, vbh = float(vb[2]), float(vb[3])
        except ValueError:
            pass
    if vbw and vbh and vbw > 0 and vbh > 0:
        if w and not h:
            h = w * vbh / vbw
        elif h and not w:
            w = h * vbw / vbh
        elif not w and not h:
            w, h = vbw, vbh
    w = w if w and w > 0 else 1024
    h = h if h and h > 0 else 1024
    scale = min(1.0, _MAX_DIM / max(w, h))
    return max(1, round(w * scale)), max(1, round(h * scale))


def _edge_cmd(tmp, *extra):
    """Edge headless with network off and a throwaway profile. (Edge itself
    blocks scripts in MHTML; --blink-settings=scriptEnabled=false would make
    screenshot/print produce nothing.)"""
    return [engines._edge(), "--headless=new", "--disable-gpu",
            "--hide-scrollbars", _NO_NET,
            f"--user-data-dir={os.path.join(tmp, 'edgeprofile')}", *extra]


def convert_vector(src, dst_ext):
    src = os.path.abspath(src)
    data = Path(src).read_bytes()
    w, h = _svg_size(data)
    b64 = base64.b64encode(data).decode("ascii")
    # <img> never runs scripts or loads external resources from the SVG;
    # the CSP is a second line of defence.
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src "
            "'none'; img-src data:; style-src 'unsafe-inline'\">"
            f"<style>@page{{size:{w}px {h}px;margin:0}}"
            "html,body{margin:0;padding:0;overflow:hidden}"
            f"img{{display:block;width:{w}px;height:{h}px}}</style></head>"
            f"<body><img src=\"data:image/svg+xml;base64,{b64}\"></body></html>")
    dst = engines.unique_path(Path(src).with_suffix("." + dst_ext))
    with tempfile.TemporaryDirectory() as tmp:
        html = os.path.join(tmp, "in.html")
        Path(html).write_text(page, encoding="utf-8")
        uri = Path(html).as_uri()
        try:
            if dst_ext == "pdf":
                engines._run(_edge_cmd(tmp, "--no-pdf-header-footer",
                                       f"--print-to-pdf={dst}", uri), "Edge")
            else:
                png = dst if dst_ext == "png" else Path(tmp) / "r.png"
                bg = "00000000" if dst_ext == "png" else "ffffffff"
                engines._run(_edge_cmd(
                    tmp, f"--default-background-color={bg}",
                    f"--window-size={w},{h}", f"--screenshot={png}", uri), "Edge")
                if not Path(png).exists():
                    raise engines.ConvertError("Edge did not render the SVG.")
                if dst_ext == "jpg":
                    from PIL import Image
                    with Image.open(png) as im:
                        im = im.convert("RGBA")
                        flat = Image.new("RGB", im.size, "white")
                        flat.paste(im, mask=im.getchannel("A"))
                        flat.save(dst, "JPEG", quality=90)
            if not dst.exists():
                raise engines.ConvertError("Edge produced no output.")
        except Exception:
            dst.unlink(missing_ok=True)
            raise
    return [str(dst)]


# ---------------------------------------------------------------- web archive

def _check_mhtml(path):
    """Refuse empty / non-MIME input (headers must say multipart/related)."""
    from email.parser import BytesHeaderParser
    with open(path, "rb") as f:
        head = f.read(65536)
    ctype = ""
    if head.strip():
        ctype = (BytesHeaderParser().parsebytes(head).get_content_type() or "")
    if ctype != "multipart/related":
        raise engines.ConvertError("Not a valid MHTML web archive.")


def convert_webarchive(src, dst_ext):
    src = os.path.abspath(src)
    _check_mhtml(src)
    dst = engines.unique_path(Path(src).with_suffix(".pdf"))
    outputs = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # Plain-named temp copy so odd names/extensions can't matter.
            local = os.path.join(tmp, "in.mhtml")
            Path(local).write_bytes(Path(src).read_bytes())
            # png: print into tmp so nothing extra appears beside the source.
            pdf = dst if dst_ext == "pdf" else Path(tmp) / "out.pdf"
            engines._run_producing(
                _edge_cmd(tmp, "--no-pdf-header-footer",
                          f"--print-to-pdf={pdf}", Path(local).as_uri()),
                "Edge", pdf)
            if not Path(pdf).exists():
                raise engines.ConvertError("Edge did not produce a PDF.")
            if dst_ext == "pdf":
                return [str(dst)]
            # png: render pages at 150 dpi, named after the source stem.
            doc = engines._pymupdf().open(str(pdf))
            try:
                stem = Path(src).with_suffix("")
                for i, page in enumerate(doc, start=1):
                    out = engines.unique_path(Path(f"{stem}_p{i}.png"))
                    page.get_pixmap(dpi=150).save(str(out))
                    outputs.append(str(out))
            finally:
                doc.close()
        return outputs
    except Exception:
        if dst_ext == "pdf":
            dst.unlink(missing_ok=True)
        for o in outputs:
            Path(o).unlink(missing_ok=True)
        raise


FAMILIES = {
    "vector": {
        "exts": {"svg"},
        "targets": [("PNG", "png"), ("PDF", "pdf"), ("JPG", "jpg")],
        "convert": convert_vector,
    },
    "webarchive": {
        "exts": {"mhtml", "mht"},
        "targets": [("PDF", "pdf"), ("PNG (pages)", "png")],
        "convert": convert_webarchive,
    },
}


_SAMPLE_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
               '<rect width="100" height="50" fill="#36c"/>'
               '<circle cx="50" cy="25" r="15" fill="#fc0"/></svg>')
_SAMPLE_MHT = (
    "From: <saved>\r\nSubject: t\r\nMIME-Version: 1.0\r\n"
    "Content-Type: multipart/related; boundary=\"B\"; type=\"text/html\"\r\n\r\n"
    "--B\r\nContent-Type: text/html; charset=utf-8\r\n"
    "Content-Location: http://example.invalid/\r\n\r\n"
    "<html><body><h1>Hello archive</h1><p>Sample page.</p></body></html>\r\n"
    "--B--\r\n")


def selftest_cases(tmp):
    def svg(ext):
        p = os.path.join(tmp, "web_s.svg")
        Path(p).write_text(_SAMPLE_SVG, encoding="utf-8")
        return convert_vector(p, ext)

    def mht(ext):
        p = os.path.join(tmp, "web_s.mhtml")
        Path(p).write_text(_SAMPLE_MHT, encoding="utf-8", newline="")
        return convert_webarchive(p, ext)

    return ([(f"vector: svg->{e}", lambda e=e: svg(e))
             for e in ("png", "pdf", "jpg")] +
            [(f"webarchive: mhtml->{e}", lambda e=e: mht(e))
             for e in ("pdf", "png")])
