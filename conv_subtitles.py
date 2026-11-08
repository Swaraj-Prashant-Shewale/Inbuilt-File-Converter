"""Subtitle conversion plugin: srt / vtt / ass / ssa -> srt / vtt / ass (FFmpeg)."""

from pathlib import Path

import engines

_CODEC = {"srt": "srt", "vtt": "webvtt", "ass": "ass"}


def _convert_subtitle(src, target):
    ff = engines._ffmpeg()
    dst = engines.unique_path(Path(src).with_suffix("." + target))
    tmp = None
    try:
        raw = Path(src).read_bytes()
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Legacy encoding: transcode here (latin-1 never fails) so FFmpeg
            # doesn't silently drop cues with cp1252-undefined bytes.
            try:
                text = raw.decode("cp1252")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            tmp = Path(src).with_name(Path(src).stem + ".qc_tmp" + Path(src).suffix)
            tmp = engines.unique_path(tmp)
            tmp.write_text(text, encoding="utf-8")
        engines._run_producing(
            [ff, "-y", "-hide_banner", "-loglevel", "error", "-i",
             str(tmp or src), "-c:s", _CODEC[target], str(dst)], "FFmpeg", dst)
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
    return [str(dst)]


FAMILIES = {
    "subtitle": {
        "exts": {"srt", "vtt", "ass", "ssa"},
        "targets": [("SRT", "srt"), ("VTT", "vtt"), ("ASS", "ass")],
        "convert": _convert_subtitle,
    },
}

_SRT = ("1\n00:00:01,000 --> 00:00:03,000\nHello world\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nSecond cue\n")


def selftest_cases(tmp):
    def case(target):
        def run():
            src = Path(tmp) / f"qc_sub_{target}.srt"
            src.write_text(_SRT, encoding="utf-8")
            return _convert_subtitle(str(src), target)
        return run
    return [(f"subtitle: srt->{t}", case(t)) for t in ("vtt", "ass", "srt")]
