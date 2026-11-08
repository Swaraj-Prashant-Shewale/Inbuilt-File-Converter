# Changelog

All notable changes to Quick Convert are documented here.

## v1.2.0 - 2026-11-08

### Added

- **Spreadsheets** (xlsx, xls, xlsm, xlsb, ods) -> PDF, PNG/JPG pages, CSV
  (one file per sheet), XLSX. Uses Excel, otherwise LibreOffice.
- **Email** (eml, msg) -> PDF or PNG/JPG, or extract the attachments. Remote
  images and scripts are blocked.
- **PDF -> split pages**: one PDF per page in a single step.
- **Old Office upgrades**: doc -> docx/pdf, ppt/pps/odp -> pptx.
- **Visio** (vsdx, vsd) -> PDF/PNG/JPG.
- **XPS/OXPS, MOBI, FB2, CBZ, CBR** -> PDF or page images.
- **SVG** -> PNG/JPG/PDF; **saved web pages** (mhtml) -> PDF/PNG.
- **Fonts**: TTF/OTF <-> WOFF/WOFF2.
- **Subtitles**: SRT <-> VTT <-> ASS.
- **Data**: JSON <-> YAML <-> XML <-> CSV/TSV, any of them -> XLSX.
- **Contacts** (vcf) and **calendars** (ics) -> CSV/JSON.
- **Images**: read PSD and camera RAW (CR2, CR3, NEF, ARW, DNG, RAF, ...);
  read and write JPEG XL.
- **Video -> PNG frames** (one per second, into a folder).
- Self-test now covers every new format and fails if any Office process is
  left running afterwards.

### Changed

- New formats live in `conv_*.py` plugin modules; a broken plugin no longer
  stops the app from starting (it is reported by `--selftest`).
- Word, Excel, PowerPoint and Visio share one automation helper: macros off,
  alerts off, a watchdog for hung (e.g. password-prompting) files, and the
  app process is force-closed if it doesn't exit after Quit.
- Security: Office automation fails closed if macros can't be disabled;
  CSV output neutralises formula-looking cells (`=`, `+`, `-`, `@`); WOFF
  and WOFF2 fonts are size-checked by content, not file name.
- Installers now require Excel too before skipping LibreOffice, and the
  source installer adds openpyxl, PyYAML, defusedxml, extract-msg, rawpy and
  pillow-jxl-plugin.

## v1.1.0 - 2026-11-04

### Added

- **Presentations** - PPTX, PPT, PPTM, PPSX, PPS and ODP to PDF, or to one
  PNG/JPG per slide (`name_slide1.png`, ...). Rendered by Microsoft PowerPoint
  if installed, otherwise LibreOffice headless.
- The window now closes automatically 5 seconds after an error-free
  conversion. Clicking "Show in folder" or adding more files cancels it.
- Self-test (`--selftest`) now covers presentation -> PDF and slide images.

### Security

- Word and PowerPoint are now driven with macros force-disabled
  (`AutomationSecurity`), so converting an untrusted .docm/.pptm cannot run
  its macros.

### Changed

- Both installers now install LibreOffice unless it is already present or
  both Microsoft Word and PowerPoint are installed.
- The source installer now also installs `python-pptx` and `pywin32`
  (pywin32 was previously missing, so Word/PowerPoint export was unavailable
  in the source edition).

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
