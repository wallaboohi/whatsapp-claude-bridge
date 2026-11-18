"""Rebuild fleet/vehicles.json from the Segment D sheet tab "רכבים" (read-only) + the older WhatsApp-built file.

  uv run -q --no-project --with google-auth --with requests python -I -X utf8 fleet/build_vehicles.py          # fresh read
  python -I -X utf8 fleet/build_vehicles.py --dump                                                              # offline, from the dump

The sheet is the source of truth for the columns it has. Info that exists only in the old WhatsApp file
(driver from the vehicle app, group, km reading, notes) is kept under "whatsapp" and never dropped.
Never writes to the sheet (gsheet.py only requests read-only scopes).
"""
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TAB = "רכבים"
DUMP = os.path.join(ROOT, "agent", "home", "sheets", "segment_d_dump.json")
OUT = os.path.join(HERE, "vehicles.json")
PREV = os.path.join(HERE, "vehicles.prev.json")

# header text (stripped) -> clean key; position decides duplicates. Unknown headers -> col_<LETTER>.
KEYS = {
    "פרויקט": "project", "רכב": "vehicle", "מספר רכב": "plate", "נהגים מורשים": "authorized_drivers",
    "שייכות": "owner", "מספר כרטיס דלק": "fuel_card", "קילומטרג תחילת חודש": "km_month_start",
    "עלות תדלוק בדצמבר": "fuel_cost_december", "כבישי אגרה בנובמבר": "tolls_november",
    "קילומטר התחלתי": "km_initial", "קילומטרג עדכני": "km_current", 'סך הכל ק"מ': "km_total",
    'עלות חריגת ק"מ': "km_overage_cost", "טיפול הבא": "next_service", "בחריגה": "overage",
    "טסט בתוקף עד": "test_valid_until", "מערכת מיגון": "protection_system", "מחיר מיגון": "protection_price",
    "צריך טסט": "needs_test", "עלות טסט": "test_cost", "עלות חודשית": "monthly_cost",
    "ביטוח בתוקף עד": "insurance_valid_until", "עלות ביטוח מקיף / צד ג": "insurance_comprehensive_cost",
    "עלות ביטוח חובה": "insurance_mandatory_cost", 'סה"כ עלות ביטוח': "insurance_total_cost",
    "חברת ביטוח": "insurance_company", 'סה"כ עלות דלק': "fuel_total_cost", 'סה"כ עלות רכב': "vehicle_total_cost",
}
DATE_KEYS = {"test_valid_until", "insurance_valid_until"}
MONEY_KEYS = {"fuel_cost_december", "tolls_november", "km_overage_cost", "protection_price", "test_cost",
              "monthly_cost", "insurance_comprehensive_cost", "insurance_mandatory_cost", "insurance_total_cost",
              "fuel_total_cost", "vehicle_total_cost"}
READING_KEYS = {"km_month_start", "km_initial", "km_current", "km_current_2", "km_total", "next_service", "overage"}
ERRORS = ("#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#ERROR!", "#NUM!")


def letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_date(text):
    """'30/04/25', '31/8/2025', '25.10.24', '03.05' (no year) -> (iso|None, problem|None)."""
    m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", text or "")
    if not m:
        return None, None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if y is None:
        return None, f"תאריך בלי שנה: {m.group(0)}"
    y = int(y) + (2000 if len(y) == 2 else 0)
    try:
        val = dt.date(y, mo, d)
    except ValueError:
        return None, f"תאריך לא אפשרי: {m.group(0)}"
    if not 2000 <= y <= 2040:
        return None, f"שנה לא סבירה: {m.group(0)}"
    return val.isoformat(), None


def parse_number(text):
    t = (text or "").replace("₪", "").replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return float(t) if "." in t else int(t)
    except ValueError:
        return None


def parse_reading(text):
    """'342,861' | '13819 שעות מנוע' | '1661 ב03.05' | '25.10.24 16886' -> {value, unit, date?}."""
    t = (text or "").strip()
    if not t:
        return None
    unit = "hours" if "שעות" in t else "km"
    date_iso, date_problem = None, None
    dm = re.search(r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?", t)
    rest = t
    if dm and (dm.group(0).count(".") + dm.group(0).count("/")) >= 1 and not re.fullmatch(r"[\d,]+", t):
        date_iso, date_problem = parse_date(dm.group(0))
        rest = t[:dm.start()] + " " + t[dm.end():]
    nm = re.search(r"\d[\d,]*", rest)
    out = {"value": int(nm.group(0).replace(",", "")) if nm else None, "unit": unit}
    if dm and rest is not t:
        out["date"] = date_iso
        if date_problem:
            out["date_problem"] = date_problem
    return out


def plate_digits(s):
    return re.sub(r"\D", "", s or "")


def fmt_plate(d):
    if len(d) == 8:
        return f"{d[:3]}-{d[3:5]}-{d[5:]}"
    if len(d) == 7:
        return f"{d[:2]}-{d[2:5]}-{d[5:]}"
    return d


def plate_pattern(old):
    """'64?…03' -> regex; '118002…' -> prefix. Returns (regex, is_partial)."""
    s = (old or "").replace("...", "…")
    if "…" not in s and "?" not in s:
        return re.compile("^" + plate_digits(s) + "$"), False
    head, _, tail = s.partition("…")
    rx = "^" + "".join(r"\d" if c == "?" else c for c in head if c.isdigit() or c == "?")
    rx += r"\d*" + "".join(r"\d" if c == "?" else c for c in tail if c.isdigit() or c == "?") + "$"
    return re.compile(rx), True


def load_sheet(use_dump):
    """-> (rows, hidden_cols, hidden_rows, colors{(r,c): hex}, source text)."""
    if not use_dump:
        try:
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            from gsheet import Sheet
            s = Sheet()
            rows = s.values(TAB)
            r = s._get(f"https://sheets.googleapis.com/v4/spreadsheets/{s.id}", ranges=f"'{TAB}'!A1:AZ{len(rows) + 5}",
                       fields="sheets.data(columnMetadata(hiddenByUser),rowMetadata(hiddenByUser),"
                              "rowData.values(effectiveFormat.backgroundColor))").json()
            d = r["sheets"][0]["data"][0]
            hc = {i for i, c in enumerate(d.get("columnMetadata", [])) if c.get("hiddenByUser")}
            hr = {i for i, c in enumerate(d.get("rowMetadata", [])) if c.get("hiddenByUser")}
            colors = {}
            for ri, row in enumerate(d.get("rowData", [])):
                for ci, c in enumerate(row.get("values", [])):
                    bg = c.get("effectiveFormat", {}).get("backgroundColor")
                    if bg is None:
                        continue
                    rgb = tuple(round(bg.get(k, 0) * 255) for k in ("red", "green", "blue"))
                    if rgb != (255, 255, 255):
                        colors[(ri, ci)] = "#%02x%02x%02x" % rgb
            return rows, hc, hr, colors, f"Google Sheet (קריאה חיה), לשונית {TAB}"
        except Exception as e:  # offline -> dump
            print(f"fresh read failed ({e}); using dump", file=sys.stderr)
    with open(DUMP, encoding="utf-8") as f:
        dump = json.load(f)
    return dump["tabs"][TAB], set(), set(), {}, f"{os.path.basename(DUMP)} ({dump.get('title')}), לשונית {TAB}"


def build(use_dump=False):
    rows, hidden_cols, hidden_rows, colors, source = load_sheet(use_dump)
    header = rows[0]
    width = max(len(r) for r in rows)
    keys, seen = [], set()
    for i in range(width):
        h = header[i].strip() if i < len(header) else ""
        k = KEYS.get(h) or f"col_{letter(i)}"
        n = 2
        base = k
        while k in seen:
            k = f"{base}_{n}"
            n += 1
        seen.add(k)
        keys.append(k)
    columns = [{"col": letter(i), "header": header[i] if i < len(header) else "", "key": keys[i],
                "hidden": i in hidden_cols} for i in range(width)]

    vehicles, problems = [], []
    plate_rows = {}
    for ri, row in enumerate(rows[1:], start=1):
        if not any(c.strip() for c in row):
            continue
        cells = {keys[i]: row[i] for i in range(len(row)) if row[i].strip()}
        raw_plate = cells.get("plate", "")
        digits = plate_digits(raw_plate)
        v = {"plate": digits or None, "plate_display": fmt_plate(digits) if digits else None,
             "name": (cells.get("vehicle") or "").strip() or None, "project": cells.get("project"),
             "owner": cells.get("owner"), "drivers": (cells.get("authorized_drivers") or "").strip() or None,
             "fuel_card": cells.get("fuel_card"), "status": None,
             "sources": ["sheet"], "sheet_row": ri + 1, "sheet_hidden_row": ri in hidden_rows,
             "raw": cells, "dates": {}, "money": {}, "readings": {}, "problems": []}
        rc = {keys[c]: hexv for (r, c), hexv in colors.items() if r == ri and c < len(keys)}
        if rc:
            v["cell_colors"] = rc
        for k, val in cells.items():
            sval = val.strip()
            if sval in ERRORS:
                v["problems"].append(f"{k}: {sval}")
                continue
            if k in DATE_KEYS:
                iso, p = parse_date(sval)
                v["dates"][k] = iso
                if p or not iso:
                    v["problems"].append(f"{k}: {p or 'לא תאריך'} ({sval})")
            elif k in MONEY_KEYS:
                v["money"][k] = parse_number(sval)
            elif k in READING_KEYS:
                rd = parse_reading(sval)
                v["readings"][k] = rd
                if rd and rd.get("date_problem"):
                    v["problems"].append(f"{k}: {rd['date_problem']} ({sval})")
        # plate sanity
        if not digits:
            v["problems"].append("אין מספר רכב")
        elif len(digits) not in (7, 8):
            v["problems"].append(f"מספר רכב באורך {len(digits)} ספרות ({raw_plate}) — כלי צמ\"ה/מספר זיהוי?")
        if raw_plate and raw_plate.strip() != digits:
            v["problems"].append(f"מספר רכב עם תווים לא ספרתיים: {raw_plate}")
        fc = plate_digits(cells.get("fuel_card", ""))
        if fc and fc == digits:
            v["problems"].append("מספר כרטיס הדלק זהה למספר הרכב")
        drv = cells.get("authorized_drivers", "")
        if re.fullmatch(r"\d{10,}", drv.strip()):
            v["problems"].append("בעמודת 'נהגים מורשים' יש מספר (כרטיס דלק?) ולא שם")
            v["drivers"] = None
        # km readings sanity: current < initial
        kc = (v["readings"].get("km_current") or {}).get("value")
        ki = (v["readings"].get("km_initial") or {}).get("value")
        if kc is not None and ki is not None and kc < ki:
            v["problems"].append(f"ק\"מ עדכני ({kc}) קטן מהתחלתי ({ki})")
        if len(row) < len(header) - 3:
            v["problems"].append(f"שורה חלקית ({len(row)} מתוך {len(header)} עמודות)")
        if digits:
            plate_rows.setdefault(digits, []).append(ri + 1)
        vehicles.append(v)
        for p in v["problems"]:
            problems.append({"row": ri + 1, "plate": digits or None, "name": v["name"], "problem": p})
    for d, rs in plate_rows.items():
        if len(rs) > 1:
            problems.append({"row": rs[0], "plate": d, "name": None, "problem": f"מספר רכב כפול בשורות {rs}"})
    for c in columns:
        if c["header"].strip() and c["key"].endswith("_2"):
            problems.append({"row": 1, "plate": None, "name": None,
                             "problem": f"כותרת כפולה '{c['header'].strip()}' בעמודה {c['col']}"})

    # ---- merge the old WhatsApp-built file ----
    old_path = PREV if os.path.exists(PREV) else OUT
    with open(old_path, encoding="utf-8") as f:
        old = json.load(f)
    if any("sheet" in (v.get("sources") or []) for v in old.get("vehicles", [])):
        raise SystemExit("vehicles.prev.json is already a sheet build; refusing to merge it as the WhatsApp file")
    matches, whatsapp_only = [], []
    by_plate = {v["plate"]: v for v in vehicles if v["plate"]}
    for o in old.get("vehicles", []):
        rx, partial = plate_pattern(o.get("plate"))
        cands = [p for p in by_plate if rx.match(p)]
        entry = {k: val for k, val in o.items()}
        if len(cands) == 1:
            v = by_plate[cands[0]]
            v["whatsapp"] = entry
            v["sources"].append("whatsapp")
            if o.get("status"):
                v["status"] = o["status"]
                v["status_source"] = "אפליקציית רכבים (צילום ווצאפ 09.11)"
            matches.append({"old_plate": o.get("plate"), "plate": cands[0], "partial": partial,
                            "sure": not partial, "old_driver": o.get("driver"), "sheet_drivers": v["drivers"]})
        else:
            whatsapp_only.append({"plate": None if partial else plate_digits(o.get("plate")) or None,
                                  "plate_display": None, "name": o.get("model"), "project": None,
                                  "owner": o.get("group"), "drivers": o.get("driver"),
                                  "status": o.get("status"), "status_source": "אפליקציית רכבים (צילום ווצאפ 09.11)",
                                  "sources": ["whatsapp"], "whatsapp": entry, "candidates": cands,
                                  "problems": ["לא נמצא בגיליון" + (f" — כמה מועמדים: {cands}" if cands else "")]})
    for w in whatsapp_only:
        if w["plate"]:
            w["plate_display"] = fmt_plate(w["plate"])
    all_v = vehicles + whatsapp_only

    out = {
        "updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": source,
        "sources_note": "sources=['sheet'] — שורה בגיליון; 'whatsapp' — מהקובץ הקודם (צילומי אפליקציית רכבים). "
                        "raw = כל התאים כפי שהם; dates = ISO; readings = ק\"מ/שעות מנוע; money = ₪.",
        "columns": columns,
        "missing_columns": ["סטטוס רכב (מושבת/זמין)", "במוסך", "רישיון רכב (נפרד מטסט)", "תאריך קריאת ק\"מ"],
        "vehicles": all_v,
        "whatsapp_matches": matches,
        "data_problems": problems,
        "open": [o for o in old.get("open", [])],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    o = build(use_dump="--dump" in sys.argv)
    print(f"{len(o['vehicles'])} vehicles ({sum('sheet' in v['sources'] for v in o['vehicles'])} from sheet), "
          f"{len(o['whatsapp_matches'])} matched, {len(o['data_problems'])} problems -> {OUT}")
