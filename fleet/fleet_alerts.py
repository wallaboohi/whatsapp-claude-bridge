"""Serious vehicle alerts for the daily report (owner 09.11.2026): out of service, in the garage,
test / vehicle license expired or expiring within 14 days, service due by km.

  python -I -X utf8 fleet/fleet_alerts.py [YYYY-MM-DD]      -> today's alerts in Hebrew
  from fleet_alerts import alerts; alerts(vehicles, today) -> [{plate, name, kind, detail, due}]

Column map (sheet tab "רכבים"): test = "טסט בתוקף עד" (dates.test_valid_until); in Israel the vehicle
license (רישיון רכב) is renewed by the test, and the sheet has no separate license column — a
dates.license_valid_until field is honoured if one is ever added. The sheet has NO status / garage column:
status comes from the vehicle app (whatsapp.status), and any cell text containing "מושבת" / "מוסך" counts.
Service by km = readings.km_current vs readings.next_service (km only; engine hours are skipped).
"""
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DAYS = 14
SERVICE_KM_MARGIN = 1000

KIND_HE = {"out_of_service": "מושבת", "garage": "במוסך", "test_expired": "טסט פג", "test_due": "טסט מתקרב",
           "license_expired": "רישיון פג", "license_due": "רישיון מתקרב", "service_overdue": "טיפול באיחור",
           "service_due": "טיפול מתקרב"}


def _date(s):
    try:
        return dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _texts(v):
    yield v.get("status") or ""
    yield (v.get("whatsapp") or {}).get("status") or ""
    yield from (str(x) for x in (v.get("raw") or {}).values())


def alerts(vehicles, today):
    if isinstance(today, str):
        today = dt.date.fromisoformat(today)
    out = []
    for v in vehicles:
        base = {"plate": v.get("plate_display") or v.get("plate") or (v.get("whatsapp") or {}).get("plate"),
                "name": " ".join(x for x in (v.get("name"), v.get("drivers") and f"({v['drivers']})") if x) or None}
        texts = list(_texts(v))
        if any("מושבת" in t for t in texts):
            out.append({**base, "kind": "out_of_service", "detail": "הרכב מסומן מושבת", "due": None})
        if any("מוסך" in t for t in texts):
            out.append({**base, "kind": "garage", "detail": "הרכב במוסך", "due": None})
        dates = v.get("dates") or {}
        for key, kind in (("test_valid_until", "test"), ("license_valid_until", "license")):
            d = _date(dates.get(key))
            if not d:
                continue
            left = (d - today).days
            label = "טסט" if kind == "test" else "רישיון רכב"
            if left < 0:
                out.append({**base, "kind": f"{kind}_expired", "due": d.isoformat(),
                            "detail": f"{label} פג ב-{d:%d.%m.%Y} (לפני {-left} ימים)"})
            elif left <= DAYS:
                out.append({**base, "kind": f"{kind}_due", "due": d.isoformat(),
                            "detail": f"{label} בתוקף עד {d:%d.%m.%Y} (עוד {left} ימים)"})
        rd = v.get("readings") or {}
        cur, nxt = rd.get("km_current") or {}, rd.get("next_service") or {}
        if cur.get("unit") == "km" and nxt.get("unit") == "km" and cur.get("value") and nxt.get("value"):
            left = nxt["value"] - cur["value"]
            if left <= SERVICE_KM_MARGIN:
                kind = "service_overdue" if left < 0 else "service_due"
                out.append({**base, "kind": kind, "due": None,
                            "detail": f"טיפול ב-{nxt['value']:,} ק\"מ, מונה {cur['value']:,} "
                                      f"({'חריגה של ' + format(-left, ',') if left < 0 else 'נותרו ' + format(left, ',')} ק\"מ)"})
    order = list(KIND_HE)
    out.sort(key=lambda a: (order.index(a["kind"]), a["due"] or ""))
    return out


def render(items, today):
    if not items:
        return f"🚗 רכבים {today:%d.%m}: אין התראות"
    lines = [f"🚗 *התראות רכבים {today:%d.%m.%Y}* ({len(items)})"]
    for a in items:
        lines.append(f"• {KIND_HE[a['kind']]} — {a['name'] or 'רכב'} {a['plate'] or ''}: {a['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    today = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    with open(os.path.join(HERE, "vehicles.json"), encoding="utf-8") as f:
        data = json.load(f)
    print(render(alerts(data["vehicles"], today), today))
