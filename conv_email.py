"""Email plugin: .eml (stdlib) and .msg (extract-msg) -> PDF / PNG / JPG / attachments.

The message is rendered to a locked-down HTML page (no scripts, no remote
content) and printed with Edge headless, like engines._doc_to_pdf_pandoc.
"""
import base64
import html
import os
import re
import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import engines

CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"

PAGE_CSS = (
    "body{font-family:Segoe UI,Arial,sans-serif;margin:1.5em;line-height:1.4;}"
    "table.hdr{border-collapse:collapse;margin-bottom:1em;width:100%;"
    "border-bottom:2px solid #888;}"
    "table.hdr td{padding:2px 8px 2px 0;vertical-align:top;font-size:10pt;}"
    "table.hdr td.k{color:#555;font-weight:bold;white-space:nowrap;width:1%;}"
    "table.hdr tr.subj td{font-size:14pt;font-weight:bold;}"
    "img{max-width:100%;height:auto;}"
)

_DROP_TAGS = {"script", "iframe", "frame", "frameset", "object", "embed",
              "applet", "base", "link", "meta", "noscript", "form"}
_SKIP_TAGS = {"html", "head", "body", "title"}  # tag dropped, content kept
_SKIP_CONTENT = {"script", "iframe", "object", "applet", "noscript", "title",
                 "frameset", "svg", "math"}      # content dropped as well
_VOID = {"br", "hr", "img", "input", "col", "area", "wbr"}
_DROP_ATTRS = {"srcset", "background", "poster", "ping", "formaction", "action"}


# ---------------------------------------------------------------- helpers

def _safe_name(name, default="attachment"):
    """A single, Windows-safe path component from an untrusted name."""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = default
    reserved = {"CON", "PRN", "AUX", "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if name.split(".")[0].upper() in reserved:
        name = "_" + name
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        ext = ext[:20]
        name = (stem[:120 - len(ext)] + ext)[:120]
    return name


def _decode(data, charset=None):
    """bytes -> str, tolerating unknown/wrong charsets."""
    for cs in (charset, "utf-8", "cp1252"):
        if not cs:
            continue
        try:
            return data.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


class _Sanitizer(HTMLParser):
    """Re-emit an HTML fragment minus scripts, handlers and remote content."""

    def __init__(self, cids):
        super().__init__(convert_charrefs=False)
        self.cids = cids
        self.out = []
        self.skip = 0  # depth inside a content-dropped element

    def _attrs(self, attrs):
        res = []
        for k, v in attrs:
            k = k.lower()
            if k.startswith("on") or k in _DROP_ATTRS:
                continue
            if "href" in k and k != "href":
                continue
            v = v or ""
            if re.match(r"\s*(javascript|vbscript):", v, re.I):
                continue
            if k == "src":
                low = v.strip().lower()
                if low.startswith("cid:"):
                    v = self.cids.get(v.strip()[4:].strip("<>").lower())
                    if not v:
                        continue
                elif not low.startswith("data:image/"):
                    continue
            elif k == "href":
                if not re.match(r"\s*(https?:|mailto:|#)", v, re.I):
                    continue
            elif k == "style" and re.search(r"url\s*\(|@import|expression",
                                            v, re.I):
                continue
            res.append(f' {k}="{html.escape(v, quote=True)}"')
        return "".join(res)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag in _DROP_TAGS or tag in _SKIP_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_startendtag(self, tag, attrs):
        if (tag in _SKIP_CONTENT or self.skip or tag in _DROP_TAGS
                or tag in _SKIP_TAGS):
            return
        self.out.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in _DROP_TAGS or tag in _SKIP_TAGS or tag in _VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        # <style> content stays raw; the CSP blocks any url() fetch anyway
        if not self.skip:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")


def _sanitize_html(text, cids):
    p = _Sanitizer(cids)
    p.feed(text)
    p.close()
    return "".join(p.out)


def _data_uri(mime, data):
    if not (mime or "").startswith("image/"):
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _render_html(headers, attachments, html_body, text_body, cids):
    """headers: [(label, value)]; attachments: [name]. Returns a full page."""
    rows = []
    for label, value in headers:
        if value:
            cls = ' class="subj"' if label == "Subject" else ""
            rows.append(f'<tr{cls}><td class="k">{label}</td>'
                        f"<td>{html.escape(str(value))}</td></tr>")
    if attachments:
        rows.append('<tr><td class="k">Attachments</td><td>'
                    + html.escape(", ".join(attachments)) + "</td></tr>")
    if html_body:
        body = _sanitize_html(html_body, cids)
    else:
        body = ('<pre style="white-space:pre-wrap;font-family:Consolas,'
                'monospace;font-size:10pt">'
                + html.escape(text_body or "") + "</pre>")
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
            f"<style>{PAGE_CSS}</style></head><body>"
            f'<table class="hdr">{"".join(rows)}</table>'
            f"<div>{body}</div></body></html>")


# ---------------------------------------------------------------- parsing

def _hdr(msg, name):
    try:
        v = msg[name]
        return str(v) if v is not None else ""
    except Exception:  # malformed header the policy cannot parse
        return ""


def _eml_parts(msg):
    """Leaf parts, not descending into attached messages."""
    if msg.is_multipart() and msg.get_content_type() != "message/rfc822":
        for sub in msg.iter_parts():
            yield from _eml_parts(sub)
    else:
        yield msg


def _eml_text(part):
    """Decode strictly: declared charset (ascii fails on 8-bit), utf-8, cp1252."""
    data = part.get_payload(decode=True)
    if isinstance(data, bytes):
        return _decode(data, part.get_content_charset())
    return part.get_content()


def _parse_eml(src):
    """-> dict(headers, html, text, cids, files=[(name, bytes)])."""
    import email  # deferred: keeps the popup's startup fast
    import email.policy
    with open(src, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    seq = []  # inline body pieces in order: (kind, content)
    cids, files = {}, []
    for part in _eml_parts(msg):
        ctype = part.get_content_type()
        fname = part.get_filename()
        disp = part.get_content_disposition()
        cid = (part.get("Content-ID") or "").strip().strip("<>").lower()
        if ctype == "message/rfc822":
            inner = part.get_payload(0) if part.is_multipart() else None
            if inner is not None:
                files.append((fname or (_hdr(inner, "Subject") or "message")
                              + ".eml", inner.as_bytes()))
            continue
        if disp != "attachment" and not fname and ctype in (
                "text/plain", "text/html"):
            seq.append((ctype.split("/")[1], _eml_text(part)))
            continue
        data = part.get_payload(decode=True)
        if data is None:
            continue
        if ctype.startswith("image/") and not cid and disp == "inline":
            seq.append(("img", _data_uri(ctype, data)))
        if cid and ctype.startswith("image/"):
            cids[cid] = _data_uri(ctype, data)
        if fname or disp == "attachment":
            files.append((fname or f"attachment.{ctype.split('/')[-1]}", data))
    htmls = [c if k == "html" else f'<img src="{c}">'
             for k, c in seq if k in ("html", "img")]
    html_body = "\n".join(htmls) if any(k == "html" for k, _ in seq) else None
    texts = [c for k, c in seq if k == "plain"]
    text_body = "\n".join(texts) if texts else None
    headers = [("From", _hdr(msg, "From")), ("To", _hdr(msg, "To")),
               ("Cc", _hdr(msg, "Cc")), ("Date", _hdr(msg, "Date")),
               ("Subject", _hdr(msg, "Subject"))]
    return dict(headers=headers, html=html_body, text=text_body,
                cids=cids, files=files)


def _parse_msg(src):
    try:
        import extract_msg
    except ImportError:
        raise engines.ConvertError(
            "extract-msg not installed. Run: pip install --user extract-msg")
    try:
        m = extract_msg.openMsg(str(src))
    except Exception as e:
        raise engines.ConvertError(f"Could not read .msg file: {e}")
    try:
        html_body = m.htmlBody
        if isinstance(html_body, bytes):
            html_body = _decode(html_body, "utf-8")
        cids, files = {}, []
        for att in m.attachments:
            data = getattr(att, "data", None)
            if not isinstance(data, (bytes, bytearray)):
                continue  # embedded messages etc.
            name = (getattr(att, "longFilename", None)
                    or getattr(att, "shortFilename", None)
                    or getattr(att, "name", None) or "attachment")
            cid = str(getattr(att, "cid", None) or "").strip("<>").lower()
            mime = getattr(att, "mimetype", None) or ""
            if cid and (mime.startswith("image/") or not mime):
                cids[cid] = _data_uri(mime or "image/png", bytes(data))
            files.append((name, bytes(data)))
        headers = [("From", m.sender), ("To", m.to), ("Cc", m.cc),
                   ("Date", str(m.date or "")), ("Subject", m.subject)]
        return dict(headers=headers, html=html_body, text=m.body,
                    cids=cids, files=files)
    finally:
        try:
            m.close()
        except Exception:
            pass


def _parse(src):
    if Path(src).suffix.lower() == ".msg":
        return _parse_msg(src)
    return _parse_eml(src)


# ---------------------------------------------------------------- converters

def _to_pdf(dst, info):
    edge = engines._edge()
    page = _render_html(info["headers"], [n for n, _ in info["files"]],
                        info["html"], info["text"], info["cids"])
    with tempfile.TemporaryDirectory() as tmp:
        html_path = os.path.join(tmp, "mail.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page)
        engines._run_producing(
            [edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={os.path.join(tmp, 'edgeprofile')}",
             f"--print-to-pdf={dst}", Path(html_path).as_uri()], "Edge", dst)
    if not Path(dst).exists():
        raise engines.ConvertError("Edge did not produce a PDF.")


def _extract_attachments(src, info):
    if not info["files"]:
        raise engines.ConvertError("This message has no attachments.")
    base = f"{Path(src).stem} attachments"
    folder, n = Path(src).with_name(base), 1
    while folder.exists():
        n += 1
        folder = Path(src).with_name(f"{base} ({n})")
    folder.mkdir()
    try:
        used = set()
        for name, data in info["files"]:
            name = _safe_name(name)
            stem, ext = os.path.splitext(name)
            n, cand = 1, name
            while cand.lower() in used:
                n += 1
                cand = f"{stem} ({n}){ext}"
            used.add(cand.lower())
            (folder / cand).write_bytes(data)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return [str(folder)]


def convert_email(src, target):
    """eml/msg -> PDF, PNG/JPG per page, or an attachments folder."""
    src = os.path.abspath(src)
    info = _parse(src)
    if target == "attachments":
        return _extract_attachments(src, info)
    if target == "pdf":
        dst = engines.unique_path(Path(src).with_suffix(".pdf"))
        try:
            _to_pdf(dst, info)
        except Exception:
            Path(dst).unlink(missing_ok=True)
            raise
        return [str(dst)]
    # png / jpg: render a temp PDF named after the source, then move the pages
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / (Path(src).stem + ".pdf")
        _to_pdf(pdf, info)
        pages = engines.convert_pdf(str(pdf), target)
        outputs = []
        try:
            for p in pages:
                dst = engines.unique_path(Path(src).with_name(Path(p).name))
                shutil.move(p, dst)
                outputs.append(str(dst))
        except Exception:
            for o in outputs:
                Path(o).unlink(missing_ok=True)
            raise
    return outputs


FAMILIES = {
    "email": {
        "exts": {"eml", "msg"},
        "targets": [("PDF", "pdf"), ("PNG (pages)", "png"),
                    ("JPG (pages)", "jpg"),
                    ("Attachments (extract)", "attachments")],
        "convert": convert_email,
    },
}


# ---------------------------------------------------------------- selftest

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "iZk9HQAAAABJRU5ErkJggg==")


def _sample_eml(tmp):
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = "Alice <alice@example.com>"
    m["To"] = "bob@example.com"
    m["Subject"] = "Self-test"
    m.set_content("Hello Bob, plain text body.")
    m.add_alternative('<p>Hello <b>Bob</b></p><img src="cid:logo1">'
                      "<script>alert(1)</script>", subtype="html")
    m.get_payload()[1].add_related(_PNG, "image", "png", cid="<logo1>")
    m.add_attachment(b"data", maintype="application",
                     subtype="octet-stream", filename="../x.bin")
    p = Path(tmp) / "selftest_mail.eml"
    p.write_bytes(bytes(m))
    return str(p)


def selftest_cases(tmp):
    return [
        ("email: eml->pdf", lambda: convert_email(_sample_eml(tmp), "pdf")),
        ("email: eml->png", lambda: convert_email(_sample_eml(tmp), "png")),
        ("email: eml->attachments",
         lambda: convert_email(_sample_eml(tmp), "attachments")),
    ]
