"""Font conversion plugin: ttf / otf / woff / woff2 <-> each other (fontTools)."""

import os
from pathlib import Path

import engines

_FLAVORS = {"woff": "woff", "woff2": "woff2"}


_MAX_SFNT = 128 * 1024 * 1024  # decompressed size cap for WOFF2 inputs


def _b128(d, i):
    """Read a UIntBase128 from d at i -> (value, next index)."""
    v = 0
    for n in range(5):
        b = d[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i
    raise ValueError("bad base128")


def _check_woff1(src):
    """Reject WOFF (zlib) decompression bombs by actually inflating each
    table in a bounded, streaming way - declared sizes can't be trusted."""
    import zlib
    size = os.path.getsize(src)
    with open(src, "rb") as f:
        head = f.read(44)
        if len(head) < 44:
            raise engines.ConvertError("Not a readable font: truncated WOFF")
        num = int.from_bytes(head[12:14], "big")
        dirs = f.read(num * 20)
        if len(dirs) < num * 20:
            raise engines.ConvertError(
                "Not a readable font: bad WOFF table directory")
        total = 0
        for i in range(num):
            e = dirs[i * 20:(i + 1) * 20]
            off = int.from_bytes(e[4:8], "big")
            comp = int.from_bytes(e[8:12], "big")
            orig = int.from_bytes(e[12:16], "big")
            if comp > orig or off + comp > size:
                raise engines.ConvertError(
                    "Not a readable font: bad WOFF table entry")
            total += orig
            if total > _MAX_SFNT:
                raise engines.ConvertError("Font too large to convert safely")
            if comp == orig:
                continue  # stored uncompressed
            f.seek(off)
            dec, out, left = zlib.decompressobj(), 0, comp
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
                try:
                    # Never inflate more than one byte past the declared size.
                    out += len(dec.decompress(chunk, orig - out + 1))
                    while dec.unconsumed_tail and out <= orig:
                        out += len(dec.decompress(dec.unconsumed_tail,
                                                  orig - out + 1))
                except zlib.error:
                    raise engines.ConvertError(
                        "Not a readable font: corrupt WOFF data")
                if out > orig:
                    raise engines.ConvertError(
                        "Not a readable font: WOFF data larger than declared")


def _check_woff2(src):
    """Reject WOFF2 decompression bombs before fontTools inflates them."""
    import brotli
    with open(src, "rb") as f:
        head = f.read(48)
        if head[:4] != b"wOF2" or len(head) < 48:
            return
        num = int.from_bytes(head[12:14], "big")
        comp = int.from_bytes(head[20:24], "big")
        f.seek(48)
        body = f.read(num * 20 + 4096 if num < 4096 else 0)
        try:
            i, total = 0, 0
            for _ in range(num):
                flags = body[i]
                i += 1
                idx = flags & 0x3F
                if idx == 0x3F:
                    i += 4
                orig, i = _b128(body, i)
                if (idx in (10, 11)) == ((flags >> 6) == 0):
                    orig, i = _b128(body, i)  # transformLength
                total += orig
        except (IndexError, ValueError):
            raise engines.ConvertError("Not a readable font: bad WOFF2 table directory")
        if total > _MAX_SFNT:
            raise engines.ConvertError("Font too large to convert safely")
        f.seek(48 + i)
        dec, out, left = brotli.Decompressor(), 0, comp
        while left > 0:
            chunk = f.read(min(4096, left))
            if not chunk:
                break
            left -= len(chunk)
            try:
                out += len(dec.process(chunk))
            except brotli.error:
                return  # let fontTools report the corruption
            if out > total or out > _MAX_SFNT:
                raise engines.ConvertError(
                    "Not a readable font: WOFF2 data larger than declared")


def _convert_font(src, target):
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        raise engines.ConvertError(
            "fontTools not installed. Run: pip install --user fonttools")
    with open(src, "rb") as f:
        magic = f.read(4)
    if target == "woff2" or magic == b"wOF2":
        try:
            import brotli  # noqa: F401
        except ImportError:
            raise engines.ConvertError(
                "brotli not installed. Run: pip install --user brotli")
    # Decide by content, not file name: a renamed WOFF/WOFF2 must not skip
    # the decompression-bomb checks.
    if magic == b"wOF2":
        _check_woff2(src)
    elif magic == b"wOFF":
        _check_woff1(src)
    dst = None
    font = None
    try:
        try:
            font = TTFont(src)
        except Exception as e:
            raise engines.ConvertError(f"Not a readable font: {e}")
        if target in _FLAVORS:
            ext = target
            font.flavor = _FLAVORS[target]
        else:
            # Desktop: CFF outlines need an .otf name, glyf outlines .ttf.
            ext = "otf" if ("CFF " in font or "CFF2" in font) else "ttf"
            font.flavor = None
        dst = engines.unique_path(Path(src).with_suffix("." + ext))
        font.save(str(dst))
        return [str(dst)]
    except Exception as e:
        if dst is not None:
            Path(dst).unlink(missing_ok=True)
        if isinstance(e, engines.ConvertError):
            raise
        raise engines.ConvertError(
            "Font conversion failed (file may be corrupt or truncated): "
            f"{str(e) or type(e).__name__}")
    finally:
        if font is not None:
            font.close()


FAMILIES = {
    "font": {
        "exts": {"ttf", "otf", "woff", "woff2"},
        "targets": [("WOFF", "woff"), ("WOFF2", "woff2"),
                    ("TTF/OTF (desktop)", "ttf")],
        "convert": _convert_font,
    },
}


def _make_ttf(path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((0, 500)); pen.lineTo((500, 500))
    pen.lineTo((500, 0)); pen.closePath()
    glyph = pen.glyph()
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({65: "A"})
    fb.setupGlyf({".notdef": glyph, "A": glyph})
    fb.setupHorizontalMetrics({".notdef": (600, 0), "A": (600, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "QCTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))
    return str(path)


def selftest_cases(tmp):
    def case(target):
        def run():
            src = Path(tmp) / f"qctest_{target}.ttf"
            _make_ttf(src)
            return _convert_font(str(src), target)
        return run
    return [(f"font: ttf->{t}", case(t)) for t in ("woff", "woff2", "ttf")]
