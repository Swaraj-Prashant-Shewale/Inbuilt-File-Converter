# Changelog

All notable changes to Quick Convert are documented here.

## v1.0.0 - 2026-08-13

First public release. A WinRAR-style right-click file converter for Windows
Explorer: right-click any file, pick a target format, and the converted copy
appears next to the original.

Available in two editions: a **Standalone** build (bundled exe, no Python
required) and a **Source** build (Python scripts, smaller download for people
who already have Python).

### Features

- One **Quick Convert** entry in the Explorer right-click menu. By default it
  lives under Windows 11's "Show more options" (Shift+F10) so the fast compact
  menu is untouched; an opt-in `--classic-menu` flag puts it at the top level.
- A popup that offers only the target formats valid for the selected file(s),
  with a quality slider for lossy image targets.
- **Images** - PNG, JPG, WEBP, GIF, BMP, TIFF, ICO, HEIC, and PDF
  (Pillow / pillow-heif).
- **Video** - MP4, MKV, MOV, AVI, WEBM, animated GIF, plus audio extraction
  (FFmpeg).
- **Audio** - MP3, WAV, FLAC, M4A, OGG, OPUS, AAC (FFmpeg).
- **Documents** - DOCX, PDF, Markdown, HTML, ODT, RTF, EPUB, TXT. DOCX/ODT/RTF
  -> PDF preserves the original formatting via Microsoft Word (if installed) or
  LibreOffice; other document conversions use Pandoc.
- **PDF** - to DOCX, TXT, or per-page PNG/JPG.
- **Archives** - create, extract, and repack ZIP, 7Z, and TAR.GZ; folders can
  be compressed directly.
- Batch conversion, combine-images-into-one-PDF, and single-window handling of
  multi-file selections.
- Converted files save beside the original and never overwrite it (name
  clashes become `name (1).ext`).
- Fast startup: the popup opens immediately (~0.3 s for the source edition),
  with multi-file selections merging into the open window as they arrive rather
  than waiting up front.
- Clean per-user install/uninstall under `HKCU` - no admin rights and no
  resident background process; the menu survives reboot and sign-out.

### Hardening

This release incorporates a multi-agent code-review pass:

- Authenticated, acknowledged single-instance handoff (a launch can no longer
  silently vanish into an unrelated process holding the port).
- UNC / credential-coercion protection on the local handoff socket.
- Correct standalone RTF output; animated GIF/WEBP frame preservation.
- Odd-dimension video support and cleanup of partial output on failure.
- Safer archive naming (compound extensions, duplicate names, dotted folders).
- Dependency version floors, including `py7zr >= 0.20.5` (CVE-2022-44900).
