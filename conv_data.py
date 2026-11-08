"""Data-format converters: JSON/YAML/XML/CSV/TSV -> each other and XLSX,
plus vCard (.vcf) and iCalendar (.ics) -> CSV/JSON.

Plugin module for engines.py (see FAMILIES / selftest_cases at the bottom).
All parsing is hand-rolled or safe-by-default: yaml.safe_load only, XML with
DTDs/entities refused, no macros/formulas executed.
"""

import csv
import datetime as dt
import io
import json
import os
import re
from pathlib import Path

import engines

MAX_BYTES = 100 * 1024 * 1024  # refuse absurd inputs (alias bombs etc.)


# ---------------------------------------------------------------- shared helpers

def _read_text(src):
    """Read a text file, sniffing BOMs; utf-8 first, cp1252 as a fallback."""
    if os.path.getsize(src) > MAX_BYTES:
        raise engines.ConvertError("File is too large to convert (over 100 MB).")
    return _decode(Path(src).read_bytes())


def _decode(raw, charset=None):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if charset:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _norm(o, budget=None):
    """Make YAML output safe for json/yaml dumps: str keys, lists not sets.
    Counts nodes so alias bombs ("billion laughs") are refused."""
    if budget is None:
        budget = [1_000_000]
    budget[0] -= 1
    if budget[0] < 0:
        raise engines.ConvertError("The YAML expands to too much data "
                                   "(alias bomb?).")
    if isinstance(o, dict):
        return {str(k): _norm(v, budget) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_norm(v, budget) for v in o]
    return o


def _publish(src, ext, writer):
    """Write via writer(path) to a unique <stem>.<ext> beside src."""
    p = Path(src)
    dst = engines.unique_path(p.with_name(p.stem + "." + ext))
    try:
        writer(dst)
    except Exception:
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass  # don't mask the original error
        raise
    return [str(dst)]


def _write_csv(dst, headers, rows):
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel
        w = csv.writer(f)
        w.writerow([engines.csv_safe(h) for h in headers])
        for r in rows:
            w.writerow(["" if v is None else engines.csv_safe(v) for v in r])


def _write_json(dst, data):
    Path(dst).write_text(json.dumps(data, ensure_ascii=False, indent=2,
                                    default=str) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- data family

def _parse_xml(text):
    """XML -> xmltodict-style dict (attributes '@x', text '#text')."""
    low = text[:200000].lower()
    if "<!doctype" in low or "<!entity" in low:
        raise engines.ConvertError("XML with a DOCTYPE/entities is refused "
                                   "for safety.")
    try:
        from defusedxml import ElementTree as ET
        parse = lambda b: ET.fromstring(b, forbid_dtd=True)
    except ImportError:
        import xml.etree.ElementTree as ET
        parse = ET.fromstring
    try:
        # text is already decoded: drop the declared encoding so it can't clash
        text = re.sub(r"\A(\s*<\?xml\b[^>]*?)\s+encoding\s*=\s*(?:\"[^\"]*\"|'[^']*')",
                      r"\1", text, count=1)
        root = parse(text.encode("utf-8"))
    except Exception as e:
        raise engines.ConvertError(f"Invalid XML: {e}")

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    def conv(el):
        kids = [k for k in el if isinstance(k.tag, str)]  # skip comments/PIs
        txt = " ".join(x.strip() for x in
                       [el.text or ""] + [k.tail or "" for k in el]
                       if x.strip())  # mixed content: text after children too
        if not kids and not el.attrib:
            return txt if txt else None
        d = {"@" + local(k): v for k, v in el.attrib.items()}
        for k in kids:
            name, val = local(k.tag), conv(k)
            if name in d:
                if not isinstance(d[name], list):
                    d[name] = [d[name]]
                d[name].append(val)
            else:
                d[name] = val
        if txt:
            d["#text"] = txt
        return d

    return {local(root.tag): conv(root)}


def _load_data(src):
    """Parse a data file into Python objects."""
    ext = engines.ext_of(src)
    text = _read_text(src)
    try:
        if ext == "json":
            return json.loads(text)
        if ext in ("yaml", "yml"):
            import yaml
            docs = list(yaml.safe_load_all(text))
            return _norm(docs[0] if len(docs) == 1 else docs)
        if ext == "xml":
            return _parse_xml(text)
    except engines.ConvertError:
        raise
    except ImportError:
        raise engines.ConvertError("PyYAML not installed. Run: "
                                   "pip install --user pyyaml")
    except Exception as e:
        raise engines.ConvertError(f"Could not parse {ext.upper()}: {e}")
    return _load_delimited(text, "\t" if ext == "tsv" else None)


def _load_delimited(text, delim):
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    csv.field_size_limit(2**30)
    rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim)
            if any(c.strip() for c in r)]
    if not rows:
        raise engines.ConvertError("The file has no rows.")
    width = max(len(r) for r in rows)
    head, seen = [], {}
    for i in range(width):
        h = rows[0][i].strip() if i < len(rows[0]) else ""
        h = h or f"column_{i + 1}"
        n = seen.get(h, 0)
        seen[h] = n + 1
        cand = h
        while n and (cand := f"{h}_{n + 1}") in seen:
            n += 1
            seen[h] = n + 1
        seen.setdefault(cand, 1)
        head.append(cand)
    return [dict(zip(head, r + [""] * (width - len(r)))) for r in rows[1:]]


def _flatten(o, prefix=""):
    """Nested dict/list -> flat {"a.b": v, "a.0": v}."""
    out = {}
    if isinstance(o, dict) and o:
        for k, v in o.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(o, list) and o:
        for i, v in enumerate(o):
            out.update(_flatten(v, f"{prefix}.{i}" if prefix else str(i)))
    elif isinstance(o, (dict, list)):
        out[prefix or "value"] = ""
    else:
        out[prefix or "value"] = o
    return out


def _table(data):
    """Reduce parsed data to (headers, rows); raise if it isn't tabular."""
    node = data
    while isinstance(node, dict) and len(node) == 1:  # {"root": {"item": [..]}}
        node = next(iter(node.values()))
    if isinstance(node, list):
        data = node
    if isinstance(data, list):
        if not data:
            raise engines.ConvertError("The file contains an empty list.")
        flat = [_flatten(i) if isinstance(i, (dict, list)) else {"value": i}
                for i in data]
    elif isinstance(data, dict):
        flat = [_flatten(data)]
    else:
        raise engines.ConvertError("A single value can't be turned into a "
                                   "table; the file needs records or an object.")
    heads = list(dict.fromkeys(k for r in flat for k in r))
    return heads, [[r.get(h, "") for h in heads] for r in flat]


_NAME_START = ("A-Za-z_\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff\u0370-\u037d"
               "\u037f-\u1fff\u200c-\u200d\u2070-\u218f\u2c00-\u2fef"
               "\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd")  # XML 1.0 (5th ed.)
_NAME_CHAR = _NAME_START + "0-9.\\-\u00b7\u0300-\u036f\u203f-\u2040"
_BAD_TAG = re.compile(f"[^{_NAME_CHAR}]")
_BAD_START = re.compile(f"[^{_NAME_START}]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _tag(name):
    t = _BAD_TAG.sub("_", str(name)).strip() or "item"
    if _BAD_START.match(t[0]):
        t = "_" + t
    return t


def _to_xml(data):
    """JSON-like data -> XML text ('@x' attributes, '#text' text honored)."""
    from xml.sax.saxutils import escape, quoteattr

    def txt(v):
        if isinstance(v, bool):
            v = str(v).lower()
        return escape(_CTRL.sub("", "" if v is None else str(v)))

    def el(name, v, ind):
        pad = "  " * ind
        name = _tag(name)
        if isinstance(v, list):
            return "".join(el(name, i, ind) for i in v) or f"{pad}<{name}/>\n"
        if isinstance(v, dict):
            used, attrs = set(), ""
            for k, a in v.items():
                if not k.startswith("@"):
                    continue
                an = base = _tag(k[1:])
                n = 1
                while an in used:  # sanitising may collide: suffix
                    n += 1
                    an = f"{base}_{n}"
                used.add(an)
                attrs += f" {an}={quoteattr(_CTRL.sub('', str(a)))}"
            kids = [(k, a) for k, a in v.items()
                    if not k.startswith("@") and k != "#text"]
            text = txt(v["#text"]) if "#text" in v else ""
            if not kids:
                return (f"{pad}<{name}{attrs}>{text}</{name}>\n" if text
                        else f"{pad}<{name}{attrs}/>\n")
            body = "".join(el(k, a, ind + 1) for k, a in kids)
            return f"{pad}<{name}{attrs}>{text}\n{body}{pad}</{name}>\n"
        if v is None:
            return f"{pad}<{name}/>\n"
        return f"{pad}<{name}>{txt(v)}</{name}>\n"

    head = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if isinstance(data, dict) and len(data) == 1:
        k, v = next(iter(data.items()))
        if not k.startswith(("@", "#")) and not isinstance(v, list):
            return head + el(k, v, 0)  # e.g. converted from XML: keep its root
    if isinstance(data, dict):
        return head + el("root", data, 0)
    body = el("item", data, 1) if isinstance(data, list) else el("value", data, 1)
    return head + "<root>\n" + body + "</root>\n"


def _write_xlsx(dst, heads, rows):
    try:
        import openpyxl
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    except ImportError:
        raise engines.ConvertError("openpyxl not installed. Run: "
                                   "pip install --user openpyxl")
    if len(heads) > 16384 or len(rows) + 1 > 1048576:
        raise engines.ConvertError("Too much data for one Excel sheet "
                                   "(limit 16,384 columns / 1,048,576 rows).")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    def put(r, c, v):
        if isinstance(v, str):  # lone surrogates -> U+FFFD
            v = ILLEGAL_CHARACTERS_RE.sub(
                "", v.encode("utf-8", "replace").decode("utf-8"))
        elif isinstance(v, dt.datetime) and v.tzinfo is not None:
            v = v.replace(tzinfo=None)  # Excel has no time zones
        elif v is not None and not isinstance(
                v, (int, float, bool, dt.date, dt.datetime)):
            v = str(v)
        cell = ws.cell(row=r, column=c, value=v)
        if isinstance(v, str) and v.startswith("="):
            cell.data_type = "s"  # never let untrusted text become a formula

    for c, h in enumerate(heads, 1):
        put(1, c, h)
    for r, row in enumerate(rows, 2):
        for c, v in enumerate(row, 1):
            put(r, c, v)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()  # save in memory so a failure never leaves dst open
    wb.save(buf)
    Path(dst).write_bytes(buf.getvalue())


def convert_data(src, target):
    """Convert json/yaml/xml/csv/tsv to json/yaml/xml/csv/xlsx."""
    data = _load_data(src)
    if target in ("csv", "xlsx"):
        heads, rows = _table(data)
        if target == "csv":
            return _publish(src, "csv", lambda d: _write_csv(d, heads, rows))
        return _publish(src, "xlsx", lambda d: _write_xlsx(d, heads, rows))
    if target == "json":
        return _publish(src, "json", lambda d: _write_json(d, data))
    if target == "yaml":
        try:
            import yaml
        except ImportError:
            raise engines.ConvertError("PyYAML not installed. Run: "
                                       "pip install --user pyyaml")
        return _publish(src, "yaml", lambda d: Path(d).write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                           default_flow_style=False), encoding="utf-8"))
    if target == "xml":
        return _publish(src, "xml", lambda d: Path(d).write_text(
            _to_xml(data), encoding="utf-8"))
    raise engines.ConvertError(f"Unsupported target: {target}")


# ---------------------------------------------------------------- vCard / iCal

_QP_LINE = re.compile(r"[^:]*;\s*(ENCODING=)?QUOTED-PRINTABLE", re.I)


def _unfold(text):
    """Unfold RFC folded lines and quoted-printable soft breaks."""
    lines = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and lines:
            lines[-1] += ln[1:]
        else:
            lines.append(ln)
    out, i = [], 0
    while i < len(lines):  # vCard 2.1 / vCal 1.0: QP value ends with "="
        ln = lines[i]
        while _QP_LINE.match(ln) and ln.endswith("=") and i + 1 < len(lines):
            i += 1
            ln = ln[:-1] + lines[i]
        out.append(ln)
        i += 1
    return out


def _split_prop(line):
    """'GRP.NAME;P=V;P2:value' -> (NAME, {param: [vals]}, value)."""
    q, colon = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            q = not q
        elif ch == ":" and not q:
            colon = i
            break
    if colon < 0:
        return None
    head, value = line[:colon], line[colon + 1:]
    parts, cur, q = [], "", False
    for ch in head:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    name = parts[0].rsplit(".", 1)[-1].strip().upper()
    params = {}
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        if not eq:  # 2.1 bare param, e.g. TEL;CELL;VOICE
            k, v = "TYPE", k
        params.setdefault(k.strip().upper(), []).extend(
            x.strip('"') for x in v.split(",") if x)
    return name, params, value


def _qp_value(value, params):
    import quopri
    flags = [x.upper() for k in ("ENCODING", "TYPE") for x in params.get(k, [])]
    if "QUOTED-PRINTABLE" in flags:
        cs = (params.get("CHARSET") or [None])[0]
        return _decode(quopri.decodestring(value.encode("latin-1", "replace")), cs)
    return value


def _unesc(s, v21=False):
    """Undo backslash escapes (vCard 2.1 only defines an escaped ';')."""
    if v21:
        return s.replace("\\;", ";")
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1),
                  s)


def _split_struct(s, v21=False):
    """Split on unescaped ';' (structured values), unescaping each part."""
    parts, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):  # an escape pair is consumed whole
            cur += s[i:i + 2]
            i += 2
            continue
        if s[i] == ";":
            parts.append(cur)
            cur = ""
        else:
            cur += s[i]
        i += 1
    parts.append(cur)
    return [_unesc(p, v21) for p in parts]


def parse_vcards(text):
    cards, cur = [], None
    for line in _unfold(text):
        u = line.strip().upper()
        if u == "BEGIN:VCARD":
            cur = []
        elif u == "END:VCARD":
            if cur is not None:
                cards.append(cur)
            cur = None
        elif cur is not None and line.strip():
            p = _split_prop(line)
            if p:
                cur.append((p[0], p[1], _qp_value(p[2], p[1])))
    return [_vcard_record(c) for c in cards]


def _labeled(val, params):
    skip = {"VOICE", "PREF", "INTERNET", "TEXT"}
    t = "/".join(x.lower() for x in params.get("TYPE", [])
                 if x.upper() not in skip)
    return f"{val} ({t})" if t else val


def _date_iso(v):
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})(?:T.*)?", v.strip())
    return f"{m[1]}-{m[2]}-{m[3]}" if m else v.strip()


def _vcard_record(props):
    rec = {"Name": "", "Organization": "", "Title": "", "Phones": [],
           "Emails": [], "Address": [], "Birthday": "", "Notes": ""}
    fn = n_name = ""
    notes = []
    v21 = any(n == "VERSION" and r.strip().startswith("2")
              for n, _, r in props)
    un = lambda x: _unesc(x, v21)
    split = lambda x: _split_struct(x, v21)
    for name, params, raw in props:
        if name == "FN":
            fn = fn or un(raw).strip()
        elif name == "N":
            p = (split(raw) + [""] * 5)[:5]  # family;given;middle;pre;suf
            n_name = " ".join(x.replace(",", " ").strip()
                              for x in (p[3], p[1], p[2], p[0], p[4]) if x.strip())
        elif name == "ORG":
            rec["Organization"] = " / ".join(x for x in split(raw) if x)
        elif name == "TITLE":
            rec["Title"] = un(raw)
        elif name == "TEL":
            v = re.sub(r"^tel:", "", un(raw).strip(), flags=re.I)
            if v:
                rec["Phones"].append(_labeled(v, params))
        elif name == "EMAIL":
            v = re.sub(r"^mailto:", "", un(raw).strip(), flags=re.I)
            if v:
                rec["Emails"].append(_labeled(v, params))
        elif name == "ADR":
            a = ", ".join(x.strip() for x in split(raw) if x.strip())
            if a:
                rec["Address"].append(_labeled(a, params))
        elif name == "BDAY":
            rec["Birthday"] = _date_iso(un(raw))
        elif name == "NOTE":
            notes.append(un(raw))
    rec["Name"] = fn or n_name
    rec["Notes"] = "\n".join(notes)
    return {k: (_CTRL.sub("", v) if isinstance(v, str)
                else [_CTRL.sub("", x) for x in v]) for k, v in rec.items()}


def _ical_dt(value, params):
    """-> (display string, date/datetime object or None, is_all_day)."""
    v = value.strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?", v)
    if not m:
        return v, None, False
    try:
        d = dt.date(int(m[1]), int(m[2]), int(m[3]))
        if m[4] is None or "DATE" in [x.upper() for x in params.get("VALUE", [])]:
            return d.isoformat(), d, True
        t = dt.datetime(d.year, d.month, d.day, int(m[4]), int(m[5]),
                        int(m[6] or 0))
    except ValueError:
        return v, None, False
    s = t.strftime("%Y-%m-%d %H:%M")
    if m[7]:
        s += " UTC"
    elif params.get("TZID"):
        s += f" ({params['TZID'][0]})"
    return s, t, False


def _duration(v):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?"
                     r"(?:(\d+)S)?)?", v.strip())
    if not m:
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    td = dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
    return -td if m[1] == "-" else td


def parse_events(text):
    events, stack, cur = [], [], None
    for line in _unfold(text):
        p = _split_prop(line) if line.strip() else None
        if not p:
            continue
        name, params, raw = p
        if name == "BEGIN":
            stack.append(raw.strip().upper())
            if stack[-1] == "VEVENT":
                cur = {}
        elif name == "END":
            if stack and stack.pop() == "VEVENT" and cur is not None:
                events.append(_event_record(cur))
                cur = None
        elif cur is not None and stack[-1] == "VEVENT" and name not in cur:
            cur[name] = (params, _qp_value(raw, params))  # skips VALARM props
    return events


def _event_record(c):
    g = lambda k: _unesc(c[k][1]) if k in c else ""
    start, sd, allday = "", None, False
    if "DTSTART" in c:
        start, sd, allday = _ical_dt(c["DTSTART"][1], c["DTSTART"][0])
    end = ""
    if "DTEND" in c:
        end, ed, _ = _ical_dt(c["DTEND"][1], c["DTEND"][0])
        if allday and sd and ed and type(ed) is type(sd) and ed > sd:
            end = (ed - dt.timedelta(days=1)).isoformat()  # DTEND is exclusive
    elif "DURATION" in c and sd is not None:
        td = _duration(c["DURATION"][1])
        if td is not None:
            if allday:
                last = sd + td - dt.timedelta(days=1 if td.days > 0 else 0)
                end = last.isoformat()
            else:
                end = (sd + td).strftime("%Y-%m-%d %H:%M")
                end += " UTC" if start.endswith("UTC") else ""
    org = ""
    if "ORGANIZER" in c:
        params, val = c["ORGANIZER"]
        mail = re.sub(r"^mailto:", "", val.strip(), flags=re.I)
        cn = (params.get("CN") or [""])[0]
        org = f"{cn} <{mail}>" if cn and mail else cn or mail
    rec = {"Summary": g("SUMMARY"), "Start": start, "End": end,
           "All-day": allday, "Location": g("LOCATION"),
           "Description": g("DESCRIPTION"), "Organizer": org,
           "RRULE": c["RRULE"][1].strip() if "RRULE" in c else ""}
    return {k: _CTRL.sub("", v) if isinstance(v, str) else v
            for k, v in rec.items()}


def _write_records(src, target, records, columns, list_cols, what):
    if not records:
        raise engines.ConvertError(f"No {what} found in this file.")
    if target == "json":
        return _publish(src, "json", lambda d: _write_json(d, records))
    rows = []
    for r in records:
        row = []
        for c in columns:
            v = r[c]
            if c in list_cols:
                v = (" | " if c == "Address" else "; ").join(v)
            elif isinstance(v, bool):
                v = "Yes" if v else "No"
            row.append(v)
        rows.append(row)
    return _publish(src, "csv", lambda d: _write_csv(d, columns, rows))


def convert_contacts(src, target):
    """vCard -> CSV (multi-values joined) or JSON (multi-values as lists)."""
    recs = parse_vcards(_read_text(src))
    cols = ["Name", "Organization", "Title", "Phones", "Emails", "Address",
            "Birthday", "Notes"]
    return _write_records(src, target, recs, cols,
                          {"Phones", "Emails", "Address"}, "contacts")


def convert_calendar(src, target):
    """iCalendar VEVENTs -> CSV or JSON. All-day end dates are inclusive."""
    recs = parse_events(_read_text(src))
    cols = ["Summary", "Start", "End", "All-day", "Location", "Description",
            "Organizer", "RRULE"]
    return _write_records(src, target, recs, cols, set(), "events")


# ---------------------------------------------------------------- plugin API

FAMILIES = {
    "data": {
        "exts": {"json", "yaml", "yml", "xml", "csv", "tsv"},
        "targets": [("JSON", "json"), ("YAML", "yaml"), ("XML", "xml"),
                    ("CSV", "csv"), ("XLSX", "xlsx")],
        "convert": convert_data,
    },
    "contacts": {
        "exts": {"vcf"},
        "targets": [("CSV", "csv"), ("JSON", "json")],
        "convert": convert_contacts,
    },
    "calendar": {
        "exts": {"ics"},
        "targets": [("CSV", "csv"), ("JSON", "json")],
        "convert": convert_calendar,
    },
}

_VCF = ("BEGIN:VCARD\r\nVERSION:2.1\r\nN:Doe;Jane;Q;Dr.;\r\n"
        "TEL;CELL;VOICE:+1 555 0100\r\nTEL;WORK:555-0101\r\n"
        "EMAIL;INTERNET:jane@example.com\r\n"
        "NOTE;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:caf=C3=A9=\r\n=0Aline2\r\n"
        "ADR;HOME:;;1 Main St;Town;ST;12345;USA\r\nBDAY:19900115\r\n"
        "END:VCARD\r\nBEGIN:VCARD\r\nVERSION:4.0\r\nFN:Acme\\, Inc\r\n"
        "ORG:Acme;Sales\r\nTITLE:Boss\r\n"
        "TEL;TYPE=\"work,voice\":tel:+1-555-0102\r\nEND:VCARD\r\n")
_ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "SUMMARY:Team sync\\, weekly\r\n"
        "DTSTART;TZID=Europe/Paris:20240305T143000\r\n"
        "DTEND;TZID=Europe/Paris:20240305T153000\r\nLOCATION:Room 1\r\n"
        "DESCRIPTION:Line one\\nLine two that is long and\r\n  folded here\r\n"
        "ORGANIZER;CN=Bob:mailto:bob@example.com\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=TU\r\n"
        "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:alarm\r\nEND:VALARM\r\n"
        "END:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:Holiday\r\n"
        "DTSTART;VALUE=DATE:20240701\r\nDTEND;VALUE=DATE:20240703\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n")


def selftest_cases(tmp):
    def mk(name, text):
        p = os.path.join(tmp, name)
        Path(p).write_bytes(text.encode("utf-8"))  # keep CRLF as written
        return p

    j = lambda: mk("st_data.json", json.dumps(
        [{"id": 1, "name": "A", "tags": ["x", "y"], "o": {"k": 1}},
         {"id": 2, "name": "B"}]))
    cases = [(f"data: json->{t}", (lambda t=t: convert_data(j(), t)))
             for t in ("yaml", "xml", "csv", "xlsx")]
    cases += [
        ("data: csv->json", lambda: convert_data(
            mk("st_data.csv", "a;b\n1;2\n3;4\n"), "json")),
        ("data: xml->json", lambda: convert_data(
            mk("st_data.xml", '<r><i k="1">a</i><i k="2">b</i></r>'), "json")),
        ("contacts: vcf->csv", lambda: convert_contacts(
            mk("st_c.vcf", _VCF), "csv")),
        ("contacts: vcf->json", lambda: convert_contacts(
            mk("st_c.vcf", _VCF), "json")),
        ("calendar: ics->csv", lambda: convert_calendar(
            mk("st_e.ics", _ICS), "csv")),
        ("calendar: ics->json", lambda: convert_calendar(
            mk("st_e.ics", _ICS), "json")),
    ]
    return cases
