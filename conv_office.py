"""Office-family converters (plugin module): spreadsheets, legacy .doc,
Visio diagrams, and the ppt -> pptx upgrade helper.

Microsoft Office (via COM) is preferred; LibreOffice is the fallback.
Everything is opened read-only with macros force-disabled.
"""
import contextlib
import csv
import os
import re
import shutil
import tempfile
from pathlib import Path

import engines


# ---------------------------------------------------------------- helpers

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)


def _safe_name(name, default="sheet"):
    """Make a name taken from inside a file safe as a Windows filename part."""
    name = _ILLEGAL.sub("_", str(name)).strip(" .")[:80].strip(" .")
    if not name:
        return default
    if _RESERVED.match(name):
        name = "_" + name
    return name


_COM_TIMEOUT = 300  # seconds before a stuck (e.g. password-prompting) app is killed


def _exe_pids(exe):
    """Running process ids for an image name (tasklist, no console window)."""
    try:
        out = engines._run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO",
                            "CSV", "/NH"], "tasklist").stdout
    except Exception:
        return set()
    return {int(row[1]) for row in csv.reader(out.splitlines())
            if len(row) > 1 and row[1].isdigit()}


def _app_pid(app, before=None, exe=None):
    """Process id of a COM Office app: via its window, else the new process."""
    try:
        import win32process
        for get in (lambda: app.Hwnd, lambda: app.HWND,
                    lambda: app.ActiveWindow.Hwnd):
            try:
                return win32process.GetWindowThreadProcessId(get())[1]
            except Exception:
                continue
    except Exception:
        pass
    if exe and before is not None:
        new = _exe_pids(exe) - before
        if len(new) == 1:
            return new.pop()
    return None


def _kill(pid):
    with contextlib.suppress(Exception):
        engines._run(["taskkill", "/F", "/PID", str(pid)], "taskkill")


def _ensure_exited(pid, grace=5.0):
    """Force-kill `pid` if it is still running `grace` seconds after Quit."""
    import time
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.25)
    _kill(pid)


def _pid_alive(pid):
    try:
        out = engines._run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV",
                            "/NH"], "tasklist").stdout
    except Exception:
        return False
    return any(len(row) > 1 and row[1] == str(pid)
               for row in csv.reader(out.splitlines()))


def _watchdog(pid, seconds):
    """Timer that force-kills a hung app so the blocked COM call errors out."""
    import threading
    if not pid:
        return None
    t = threading.Timer(seconds, lambda: _kill(pid))
    t.daemon = True
    t.start()
    return t


@contextlib.contextmanager
def _com_app(progid, label):
    """Start an Office app via COM with macros/alerts off; always quit it."""
    try:
        import pythoncom
        import win32com.client as win32
    except ImportError:
        raise engines.ConvertError(
            f"{label} automation unavailable (pywin32 missing).")
    pythoncom.CoInitialize()
    app = None
    timer = None
    pid = None
    exe = {"Excel": "EXCEL.EXE", "Word": "WINWORD.EXE",
           "PowerPoint": "POWERPNT.EXE", "Visio": "VISIO.EXE"}.get(label)
    before = _exe_pids(exe) if exe else None
    try:
        try:
            app = win32.DispatchEx(progid)
        except Exception:
            raise engines.ConvertError(f"{label} is not installed.")
        # Automation-launched Office enables macros by default; force them
        # off before opening anything untrusted.
        # Fail closed: never open an untrusted file with macros possibly on.
        # Visio has no AutomationSecurity; its opens pass macros-disabled flags.
        if label != "Visio":
            try:
                app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
                ok = app.AutomationSecurity == 3
            except Exception:
                ok = False
            if not ok:
                raise engines.ConvertError(
                    f"{label}: could not disable macros; refusing to open the file.")
        try:
            # ppAlertsNone is 1 (0 would mean ppAlertsAll); others use 0/False.
            app.DisplayAlerts = 1 if "PowerPoint" in progid else 0
        except Exception:
            pass
        pid = _app_pid(app, before, exe)
        timer = _watchdog(pid, _COM_TIMEOUT)
        yield app
    finally:
        if timer is not None:
            timer.cancel()
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        # Quit only takes effect once every COM reference is released; drop
        # ours, then make sure this exact process is really gone so hidden
        # Office instances can never pile up.
        app = None
        import gc
        gc.collect()
        pythoncom.CoUninitialize()
        if pid:
            _ensure_exited(pid)


def _com_run(progid, label, work):
    """Run work(app) inside _com_app; wrap stray COM errors in ConvertError."""
    try:
        with _com_app(progid, label) as app:
            return work(app)
    except engines.ConvertError:
        raise
    except Exception as e:
        if "password" in str(e).lower() or "Open method" in str(e):
            raise engines.ConvertError(f"{label} could not open the file "
                                       "(password-protected or corrupt).")
        if "remote procedure call" in str(e).lower():  # watchdog killed it
            raise engines.ConvertError(f"{label} stopped responding (the file "
                                       "may be password-protected).")
        raise engines.ConvertError(f"{label} conversion failed: {e}")


def _reject_encrypted(src):
    """Encrypted OOXML is a CFB container, not a zip; Office would prompt for
    a password (and hang the hidden app), so fail fast instead."""
    if engines.ext_of(src) in ("xlsx", "xlsm", "xlsb", "docx", "pptx", "ppsx"):
        with open(src, "rb") as f:
            if f.read(4) == bytes([0xD0, 0xCF, 0x11, 0xE0]):
                raise engines.ConvertError("File is password-protected.")


def _close(obj, *args):
    try:
        if obj is not None:
            obj.Close(*args)
    except Exception:
        pass


def _soffice():
    return engines._bundled("soffice.exe") or engines._find_tool("soffice", (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ))


def _soffice_convert(src, fmt, outdir):
    """LibreOffice --convert-to `fmt` into outdir; returns produced files."""
    soffice = _soffice()
    if not soffice:
        raise engines.ConvertError("LibreOffice not found.")
    prof = (Path(outdir) / "_profile").as_uri()
    engines._run([soffice, "-env:UserInstallation=" + prof, "--headless",
                  "--norestore", "--convert-to", fmt, "--outdir",
                  str(outdir), str(src)], "LibreOffice")
    return [p for p in Path(outdir).iterdir() if p.is_file()]


_NEED_LO = ("\nInstall LibreOffice with: "
            "winget install TheDocumentFoundation.LibreOffice")


def _first_working(label, attempts):
    """Try each (callable) in turn; combine the first-line errors if all fail."""
    errors = []
    for fn in attempts:
        try:
            return fn()
        except engines.ConvertError as e:
            errors.append(str(e).splitlines()[0])
    missing = ("not installed", "not found", "automation unavailable")
    real = [e for e in errors if not any(m in e.lower() for m in missing)]
    if real:  # an app was available but the file itself failed
        raise engines.ConvertError(real[0])
    raise engines.ConvertError(f"{label} needs the matching Microsoft Office "
                               "app or LibreOffice.\n" + "\n".join(errors)
                               + _NEED_LO)


def _finish(produced, src, ext):
    """Move a temp output beside the source under a unique name."""
    dst = engines.unique_path(Path(src).with_suffix("." + ext))
    shutil.move(str(produced), str(dst))
    return [str(dst)]


def _via_pdf(src, ext, make_pdf):
    """PDF-render then rasterize pages (png/jpg) beside the source.

    make_pdf(src, tmp_pdf_path) must write the PDF at tmp_pdf_path.
    """
    src = os.path.abspath(src)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = os.path.join(tmp, _safe_name(Path(src).stem, "doc") + ".pdf")
        make_pdf(src, pdf)
        if not os.path.exists(pdf):
            raise engines.ConvertError("No PDF was produced.")
        if ext == "pdf":
            return _finish(pdf, src, "pdf")
        outputs = []
        try:
            for page in engines.convert_pdf(pdf, ext):
                p = Path(page)
                num = p.stem.rpartition("_p")[2]
                dst = engines.unique_path(
                    Path(src).with_name(f"{Path(src).stem}_p{num}.{ext}"))
                shutil.move(str(p), str(dst))
                outputs.append(str(dst))
        except Exception:
            for o in outputs:
                Path(o).unlink(missing_ok=True)
            raise
        return outputs


def _soffice_pdf_to(src, dst, pdf_filter):
    """engines' LibreOffice PDF export, relocated to the exact path `dst`."""
    made = engines._office_pdf_soffice(src, pdf_filter)[0]
    shutil.move(made, dst)


# ---------------------------------------------------------------- spreadsheets

def _open_workbook(app, src):
    # Dummy passwords make encrypted files fail fast instead of prompting.
    # Positional on purpose: pywin32 keyword args for Password hang Excel.
    # (Filename, UpdateLinks, ReadOnly, Format, Password, WriteResPassword,
    #  IgnoreReadOnlyRecommended, Origin, Delimiter, Editable, Notify,
    #  Converter, AddToMru)
    pw = "qc-no-password"
    return app.Workbooks.Open(str(src), 0, True, None, pw, pw, True, None,
                              None, None, None, None, False)


def _sheet_pdf_excel(src, dst):
    def work(app):
        wb = _open_workbook(app, src)
        try:
            wb.ExportAsFixedFormat(0, str(dst))  # 0 = xlTypePDF
        finally:
            _close(wb, False)
    _com_run("Excel.Application", "Excel", work)


def _sheet_pdf(src, dst):
    _first_working("Spreadsheet PDF export", [
        lambda: _sheet_pdf_excel(src, dst),
        lambda: _soffice_pdf_to(src, dst, "calc_pdf_Export"),
    ])


def _xlsx_excel(src, dst):
    def work(app):
        wb = _open_workbook(app, src)
        try:
            wb.SaveAs(str(dst), 51)  # 51 = xlOpenXMLWorkbook
        finally:
            _close(wb, False)
    _com_run("Excel.Application", "Excel", work)


def _lo_convert_one(src, fmt, ext):
    """LibreOffice convert to a single file `ext`; returns the temp path."""
    outdir = tempfile.mkdtemp(prefix="qc-lo-")
    try:
        files = [f for f in _soffice_convert(src, fmt, outdir)
                 if f.suffix.lower() == "." + ext]
        if not files:
            raise engines.ConvertError(f"LibreOffice produced no .{ext}.")
        keep = Path(tempfile.mkstemp(suffix="." + ext, prefix="qc-")[1])
        shutil.move(str(files[0]), str(keep))
        return keep
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def _spreadsheet_to_xlsx(src):
    src = os.path.abspath(src)
    tmpdir = tempfile.mkdtemp(prefix="qc-xl-")
    try:
        tmp = os.path.join(tmpdir, "out.xlsx")

        def lo():
            made = _lo_convert_one(src, "xlsx:Calc MS Excel 2007 XML", "xlsx")
            shutil.move(str(made), tmp)

        _first_working("Spreadsheet conversion",
                       [lambda: _xlsx_excel(src, tmp), lo])
        return _finish(tmp, src, "xlsx")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _csv_openpyxl(src):
    """[(sheet name, temp csv path)] from xlsx/xlsm cached values."""
    try:
        import openpyxl
    except ImportError:
        raise engines.ConvertError(
            "openpyxl not installed. Run: pip install --user openpyxl")
    tmpdir = tempfile.mkdtemp(prefix="qc-csv-")
    wb = None
    try:
        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        sheets = [ws for ws in wb.worksheets
                  if getattr(ws, "sheet_state", "visible") == "visible"]
        sheets = sheets or list(wb.worksheets)
        out = []
        for i, ws in enumerate(sheets):
            path = os.path.join(tmpdir, f"{i}.csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                for row in ws.iter_rows(values_only=True):
                    w.writerow(["" if v is None else engines.csv_safe(v)
                                for v in row])
            out.append((ws.title, path))
        return out
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise engines.ConvertError(f"openpyxl could not read the workbook: {e}")
    finally:
        if wb is not None:
            wb.close()


def _csv_excel(src):
    """[(sheet name, temp csv path)] via Excel, one SaveAs per visible sheet."""
    tmpdir = tempfile.mkdtemp(prefix="qc-csv-")

    def work(app):
        wb = _open_workbook(app, src)
        out = []
        try:
            # SaveAs renames the sheet to the file name, so read names first.
            names = [(i, str(wb.Worksheets(i).Name))
                     for i in range(1, wb.Worksheets.Count + 1)
                     if wb.Worksheets(i).Visible == -1]  # xlSheetVisible
            for i, name in names:
                wb.Worksheets(i).Activate()
                path = os.path.join(tmpdir, f"{i}.csv")
                wb.SaveAs(path, 62)  # 62 = xlCSVUTF8
                out.append((name, path))
        finally:
            _close(wb, False)
        return out

    try:
        return _com_run("Excel.Application", "Excel", work)
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _csv_soffice(src):
    """[(sheet name, temp csv path)] via LibreOffice (all sheets, -1 token)."""
    outdir = tempfile.mkdtemp(prefix="qc-csv-")
    try:
        _soffice_convert(src, "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,"
                         "false,true,false,false,false,-1", outdir)
        stem = Path(src).stem
        out = []
        for f in sorted(Path(outdir).glob("*.csv")):
            name = f.stem[len(stem) + 1:] if f.stem.startswith(stem + "-") \
                else f.stem
            out.append((name, str(f)))
        if not out:
            raise engines.ConvertError("LibreOffice produced no CSV.")
        return out
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True)
        raise


def _spreadsheet_to_csv(src):
    src = os.path.abspath(src)
    ext = engines.ext_of(src)
    attempts = [lambda: _csv_excel(src), lambda: _csv_soffice(src)]
    if ext in ("xlsx", "xlsm"):
        attempts.insert(0, lambda: _csv_openpyxl(src))
    sheets = _first_working("CSV export", attempts)
    outputs = []
    try:
        for name, tmp in sheets:
            if len(sheets) == 1:
                dst = Path(src).with_suffix(".csv")
            else:
                dst = Path(src).with_name(
                    f"{Path(src).stem}_{_safe_name(name)}.csv")
            dst = engines.unique_path(dst)
            shutil.move(tmp, str(dst))
            outputs.append(str(dst))
    except Exception:
        for o in outputs:
            Path(o).unlink(missing_ok=True)
        raise
    finally:
        for _, tmp in sheets:
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    return outputs


def convert_spreadsheet(src, target):
    _reject_encrypted(src)
    if target == "csv":
        return _spreadsheet_to_csv(src)
    if target == "xlsx":
        return _spreadsheet_to_xlsx(src)
    if target in ("pdf", "png", "jpg"):
        return _via_pdf(src, target, _sheet_pdf)
    raise engines.ConvertError(f"Unsupported spreadsheet target: {target}")


# ---------------------------------------------------------------- legacy .doc

def _doc_to_docx_word(src, dst):
    def work(app):
        doc = app.Documents.Open(str(src), ReadOnly=True,
                                 PasswordDocument="qc-no-password",
                                 PasswordTemplate="qc-no-password",
                                 AddToRecentFiles=False,
                                 ConfirmConversions=False)
        try:
            doc.SaveAs2(str(dst), FileFormat=16)  # 16 = wdFormatXMLDocument
        finally:
            _close(doc, False)
    _com_run("Word.Application", "Word", work)


def _doc_pdf_word(src, dst):
    def work(app):
        doc = app.Documents.Open(str(src), ReadOnly=True,
                                 PasswordDocument="qc-no-password",
                                 PasswordTemplate="qc-no-password",
                                 AddToRecentFiles=False,
                                 ConfirmConversions=False)
        try:
            doc.ExportAsFixedFormat(str(dst), 17)  # 17 = wdExportFormatPDF
        finally:
            _close(doc, False)
    _com_run("Word.Application", "Word", work)


def _doc_to_pdf(src):
    tmpdir = tempfile.mkdtemp(prefix="qc-doc-")
    try:
        tmp = os.path.join(tmpdir, "out.pdf")
        _first_working("DOC to PDF", [
            lambda: _doc_pdf_word(src, tmp),
            lambda: _soffice_pdf_to(src, tmp, "writer_pdf_Export"),
        ])
        return _finish(tmp, src, "pdf")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _doc_to_docx(src):
    src = os.path.abspath(src)
    tmpdir = tempfile.mkdtemp(prefix="qc-doc-")
    try:
        tmp = os.path.join(tmpdir, "out.docx")

        def lo():
            made = _lo_convert_one(src, "docx:MS Word 2007 XML", "docx")
            shutil.move(str(made), tmp)

        _first_working("DOC conversion",
                       [lambda: _doc_to_docx_word(src, tmp), lo])
        return _finish(tmp, src, "docx")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def convert_legacy_doc(src, target):
    src = os.path.abspath(src)
    _reject_encrypted(src)
    if target == "docx":
        return _doc_to_docx(src)
    if target == "pdf":
        return _doc_to_pdf(src)
    raise engines.ConvertError(f"Unsupported document target: {target}")


# ---------------------------------------------------------------- diagrams

def _diagram_pdf_visio(src, dst):
    def work(app):
        try:
            app.EventsEnabled = False
        except Exception:
            pass
        # visOpenRO=2 | visOpenDontList=8 | visOpenHidden=64 | visOpenMacrosDisabled=128
        doc = app.Documents.OpenEx(str(src), 2 | 8 | 64 | 128)
        try:
            # visFixedFormatPDF=1, visDocExIntentPrint=1, visPrintAll=0
            doc.ExportAsFixedFormat(1, str(dst), 1, 0)
        finally:
            _close(doc)
    _com_run("Visio.InvisibleApp", "Visio", work)


def _diagram_pdf(src, dst):
    _first_working("Diagram conversion", [
        lambda: _diagram_pdf_visio(src, dst),
        lambda: _soffice_pdf_to(src, dst, "draw_pdf_Export"),
    ])


def convert_diagram(src, target):
    if target not in ("pdf", "png", "jpg"):
        raise engines.ConvertError(f"Unsupported diagram target: {target}")
    return _via_pdf(src, target, _diagram_pdf)


# ---------------------------------------------------------------- presentations

def _pptx_powerpoint(src, dst):
    def work(app):
        # "::password::" suffix makes encrypted files fail fast, not prompt.
        pres = app.Presentations.Open(str(src) + "::qc-no-password::",
                                      ReadOnly=True, Untitled=False,
                                      WithWindow=False)
        try:
            pres.SaveAs(str(dst), 24)  # 24 = ppSaveAsOpenXMLPresentation
        finally:
            _close(pres)
    _com_run("PowerPoint.Application", "PowerPoint", work)


def upgrade_presentation(src, dst_ext="pptx"):
    """ppt/pps/odp/ppsx -> pptx via PowerPoint (LibreOffice fallback)."""
    if dst_ext != "pptx":
        raise engines.ConvertError("Only pptx upgrade is supported.")
    src = os.path.abspath(src)
    _reject_encrypted(src)
    tmpdir = tempfile.mkdtemp(prefix="qc-ppt-")
    try:
        tmp = os.path.join(tmpdir, "out.pptx")

        def lo():
            made = _lo_convert_one(src, "pptx:Impress MS PowerPoint 2007 XML",
                                   "pptx")
            shutil.move(str(made), tmp)

        _first_working("Presentation upgrade",
                       [lambda: _pptx_powerpoint(src, tmp), lo])
        return _finish(tmp, src, "pptx")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- registration

FAMILIES = {
    "spreadsheet": {
        "exts": {"xlsx", "xls", "xlsm", "xlsb", "ods"},
        "targets": [("PDF", "pdf"), ("PNG (pages)", "png"),
                    ("JPG (pages)", "jpg"), ("CSV (per sheet)", "csv"),
                    ("XLSX", "xlsx")],
        "convert": convert_spreadsheet,
    },
    "legacy_doc": {
        "exts": {"doc"},
        "targets": [("DOCX", "docx"), ("PDF", "pdf")],
        "convert": convert_legacy_doc,
    },
    "diagram": {
        "exts": {"vsdx", "vsd"},
        "targets": [("PDF", "pdf"), ("PNG", "png"), ("JPG", "jpg")],
        "convert": convert_diagram,
    },
}


# ---------------------------------------------------------------- selftest

def _make_xlsx(tmp):
    import openpyxl
    p = os.path.join(tmp, "office_t.xlsx")
    wb = openpyxl.Workbook()
    wb.active.title = "One"
    wb.active.append(["a", "b"])
    wb.active.append([1, 2])
    wb.create_sheet("Two").append(["x", "y"])
    wb.save(p)
    return p


def _make_legacy(tmp, progid, label, build, name, fmt):
    """Create a legacy-format sample through an Office app (fmt = save code)."""
    p = os.path.join(tmp, name)

    def work(app):
        build(app, p, fmt)
    _com_run(progid, label, work)
    return p


def _mk_xls(tmp):
    def build(app, p, fmt):
        wb = app.Workbooks.Add()
        wb.Worksheets(1).Range("A1").Value = "hi"
        wb.SaveAs(p, fmt)  # 56 = xlExcel8
        wb.Close(False)
    return _make_legacy(tmp, "Excel.Application", "Excel", build,
                        "office_t.xls", 56)


def _mk_doc(tmp):
    def build(app, p, fmt):
        d = app.Documents.Add()
        d.Content.Text = "Quick Convert legacy doc"
        d.SaveAs2(p, FileFormat=fmt)  # 0 = wdFormatDocument
        d.Close(False)
    return _make_legacy(tmp, "Word.Application", "Word", build,
                        "office_t.doc", 0)


def _mk_ppt(tmp):
    def build(app, p, fmt):
        pr = app.Presentations.Add(WithWindow=False)
        pr.Slides.Add(1, 12)  # ppLayoutBlank
        pr.SaveAs(p, fmt)  # 1 = ppSaveAsPresentation
        pr.Close()
    return _make_legacy(tmp, "PowerPoint.Application", "PowerPoint", build,
                        "office_t.ppt", 1)


def selftest_cases(tmp):
    return [
        ("office: xlsx->pdf", lambda: convert_spreadsheet(_make_xlsx(tmp), "pdf")),
        ("office: xlsx->csv", lambda: convert_spreadsheet(_make_xlsx(tmp), "csv")),
        ("office: xls->xlsx", lambda: convert_spreadsheet(_mk_xls(tmp), "xlsx")),
        ("office: doc->docx", lambda: convert_legacy_doc(_mk_doc(tmp), "docx")),
        ("office: doc->pdf", lambda: convert_legacy_doc(_mk_doc(tmp), "pdf")),
        ("office: ppt->pptx", lambda: upgrade_presentation(_mk_ppt(tmp))),
    ]
