"""Fixed-layout / ebook / comic formats: XPS, OXPS, MOBI, FB2, CBZ, CBR."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

import engines

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def _fitz():
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # older PyMuPDF name
        except ImportError:
            raise engines.ConvertError("PyMuPDF not installed. "
                                       "Run: pip install --user pymupdf")
    return fitz


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", str(s))]


def _render_pages(doc, src, ext):
    """Render every page to <stem>_pN.<ext> at 150 dpi."""
    outputs = []
    stem = Path(src).with_suffix("")
    try:
        for i, page in enumerate(doc, start=1):
            out = engines.unique_path(Path(f"{stem}_p{i}.{ext}"))
            outputs.append(str(out))
            page.get_pixmap(dpi=150).save(str(out))
    except Exception:
        for o in outputs:
            Path(o).unlink(missing_ok=True)
        raise
    return outputs


def _convert_native(src, target):
    """XPS/OXPS/MOBI/FB2/CBZ via PyMuPDF."""
    fitz = _fitz()
    try:
        doc = fitz.open(str(src))
    except Exception as e:
        raise engines.ConvertError(f"Could not open file: {e}")
    try:
        if doc.is_encrypted:
            raise engines.ConvertError("File is password protected.")
        if doc.is_reflowable:  # ebooks: lay out on A5 pages first
            doc.layout(rect=fitz.paper_rect("a5"), fontsize=11)
        if doc.page_count == 0:
            raise engines.ConvertError("No pages found in this file.")
        if target != "pdf":
            return _render_pages(doc, src, target)
        dst = engines.unique_path(Path(src).with_suffix(".pdf"))
        try:
            dst.write_bytes(doc.convert_to_pdf())
        except Exception:
            dst.unlink(missing_ok=True)
            raise
        return [str(dst)]
    finally:
        doc.close()


MAX_MEMBERS = 5000
MAX_BYTES = 2 * 1024 ** 3  # total uncompressed image bytes (decompression-bomb cap)
_TAR_LINE = re.compile(r"^\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s")


def _tar(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=engines.CREATE_NO_WINDOW)


def _extract_rar(src, dest):
    """Extract only image members of a RAR with Windows' bsdtar into `dest`."""
    # prefer Windows' bsdtar: a GNU tar earlier on PATH (e.g. Git) can't read RAR
    tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "tar.exe")
    if not os.path.isfile(tar):
        raise engines.ConvertError(
            "tar.exe not found (needed to read CBR). Requires Windows 10 or later.")
    pats = []
    for e in sorted(IMG_EXTS):
        pats += ["--include", f"*{e}", "--include", f"*{e.upper()}"]
    # list first: refuse oversized / huge archives before writing anything
    # unmatched patterns make tar exit non-zero, so judge by output, not status
    lst = _tar(tar, "-tvf", str(src), *pats)
    if lst.returncode and not lst.stdout.strip() and "Not found" not in lst.stderr:
        raise engines.ConvertError("tar failed: " + lst.stderr.strip()[-400:])
    lst = [ln for ln in lst.stdout.splitlines() if ln.strip()]
    total = 0
    for ln in lst:
        m = _TAR_LINE.match(ln)
        total += int(m.group(1)) if m else 0
    if len(lst) > MAX_MEMBERS or total > MAX_BYTES:
        raise engines.ConvertError("Comic archive is too large to convert safely.")
    ext = _tar(tar, "-xf", str(src), "-C", str(dest), *pats)
    if ext.returncode and "Not found" not in ext.stderr:
        raise engines.ConvertError("tar failed: " + ext.stderr.strip()[-400:])


def _extract_zip(src, dest):
    """Copy image members of a CBZ to numbered files in `dest` (names never
    reach the filesystem); caps member count and real bytes written."""
    import zipfile
    try:
        z = zipfile.ZipFile(str(src))
    except Exception as e:
        raise engines.ConvertError(f"Could not open file: {e}")
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()
                 and Path(i.filename).suffix.lower() in IMG_EXTS]
        if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_BYTES:
            raise engines.ConvertError("Comic archive is too large to convert safely.")
        infos.sort(key=lambda i: _natkey(i.filename))
        left = MAX_BYTES
        for n, i in enumerate(infos):
            out = Path(dest) / f"{n:05d}{Path(i.filename).suffix.lower()}"
            try:
                with z.open(i) as r, open(out, "wb") as w:
                    while True:
                        buf = r.read(1 << 20)
                        if not buf:
                            break
                        left -= len(buf)
                        if left < 0:
                            raise engines.ConvertError(
                                "Comic archive is too large to convert safely.")
                        w.write(buf)
            except engines.ConvertError:
                raise
            except Exception as e:
                raise engines.ConvertError(f"Could not read comic archive: {e}")


def _comic_images(root):
    """Image files under `root`, naturally sorted; no links or escapes."""
    root = Path(root).resolve()
    found = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = Path(dp) / f
            if p.suffix.lower() not in IMG_EXTS or p.is_symlink():
                continue
            try:
                rp = p.resolve()
                rp.relative_to(root)  # traversal guard
            except (ValueError, OSError):
                continue
            found.append(rp)
    return sorted(found, key=lambda p: _natkey(p.relative_to(root).as_posix()))


def _insert_image(out, p, fitz):
    """Append one image as a PDF page; Pillow fallback for formats MuPDF lacks."""
    try:
        with fitz.open(str(p)) as idoc:
            out.insert_pdf(fitz.open("pdf", idoc.convert_to_pdf()))
        return
    except Exception:
        pass
    import io
    from PIL import Image
    with Image.open(p) as im:
        im = engines._flatten(im)
        buf = io.BytesIO()
        im.save(buf, "PNG")
    with fitz.open("png", buf.getvalue()) as idoc:
        out.insert_pdf(fitz.open("pdf", idoc.convert_to_pdf()))


def _convert_comic(src, target):
    """CBZ/CBR: extract image pages safely, then build a PDF or page images."""
    fitz = _fitz()
    with tempfile.TemporaryDirectory(prefix="qc_cbr_") as tmp:
        err = None
        try:
            if engines.ext_of(src) == "cbz":
                _extract_zip(src, tmp)
            else:
                _extract_rar(src, tmp)
        except engines.ConvertError as e:
            if "too large" in str(e) or engines.ext_of(src) == "cbz":
                raise
            err = e  # bsdtar skips unsafe members (".."), exiting non-zero
        imgs = _comic_images(tmp)
        if not imgs and err:
            raise err
        if not imgs:
            raise engines.ConvertError("No images found in this comic archive.")
        stem = Path(src).with_suffix("")
        if target == "pdf":
            dst = engines.unique_path(Path(f"{stem}.pdf"))
            out = fitz.open()
            try:
                bad = 0
                for p in imgs:
                    try:
                        _insert_image(out, p, fitz)
                    except Exception:
                        bad += 1
                if not out.page_count:
                    raise engines.ConvertError("No readable images in archive.")
                if bad:
                    raise engines.ConvertError(
                        f"{bad} page(s) could not be read; conversion aborted.")
                out.save(str(dst), deflate=True)
            except Exception:
                Path(dst).unlink(missing_ok=True)
                raise
            finally:
                out.close()
            return [str(dst)]
        from PIL import Image
        outputs = []
        try:
            for i, p in enumerate(imgs, start=1):
                out = engines.unique_path(Path(f"{stem}_p{i}.{target}"))
                outputs.append(str(out))
                with Image.open(p) as im:
                    if target == "jpg":
                        engines._flatten(im).convert("RGB").save(out, quality=85)
                    else:
                        if im.mode not in ("1", "L", "LA", "I", "I;16", "P",
                                           "RGB", "RGBA"):
                            im = im.convert("RGBA" if "A" in im.mode else "RGB")
                        im.save(out)
        except engines.ConvertError:
            raise
        except Exception as e:
            for o in outputs:
                Path(o).unlink(missing_ok=True)
            raise engines.ConvertError(f"Could not convert page: {e}")
        return outputs


def convert(src, target):
    if engines.ext_of(src) in ("cbz", "cbr"):
        return _convert_comic(src, target)
    return _convert_native(src, target)


FAMILIES = {
    "fixed_layout": {
        "exts": {"xps", "oxps", "mobi", "fb2", "cbz", "cbr"},
        "targets": [("PDF", "pdf"), ("PNG (pages)", "png"),
                    ("JPG (pages)", "jpg")],
        "convert": convert,
    },
}


def selftest_cases(tmp):
    tmp = Path(tmp)

    def fb2():
        p = tmp / "t.fb2"
        p.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
            '<description><title-info><book-title>T</book-title></title-info>'
            '</description><body><section><title><p>Hi</p></title>'
            '<p>Hello world.</p></section></body></FictionBook>',
            encoding="utf-8")
        return convert(str(p), "pdf")

    def cbz():
        import zipfile
        from PIL import Image
        p = tmp / "t.cbz"
        with zipfile.ZipFile(p, "w") as z:
            for i in (1, 2, 10):
                f = tmp / f"pg{i}.png"
                Image.new("RGB", (100, 150), (i * 20, 0, 0)).save(f)
                z.write(f, f"pg{i}.png")
        return convert(str(p), "png")

    return [("fixedlayout: fb2->pdf", fb2), ("fixedlayout: cbz->png", cbz)]
