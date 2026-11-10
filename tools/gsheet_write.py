"""Write cells in the company Google Sheet — ONLY after the owner approved the exact numbered changes (owner 10.11.2026).

  gsheet_write.py <changes.json>               preview: shows old -> new for every cell, writes nothing
  gsheet_write.py <changes.json> --approved    writes (USER_ENTERED), saves the old values for undo, logs to actions.log
  gsheet_write.py --undo <backup.json> --approved   puts the saved old values back

changes.json (UTF-8): [{"tab": "משכורות", "cell": "AB7", "value": "מילואים"}, ...]
Find the cell with `gsheet.py values <tab>` first (row/column of the name and the date). gsheet.py itself stays read-only.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsheet  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(os.path.expanduser("~"), "WhatsApp-Backup", "google-sheet-writes")
WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def session():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    with open(gsheet.KEY, encoding="utf-8") as f:
        info = json.load(f)
    return AuthorizedSession(service_account.Credentials.from_service_account_info(info, scopes=WRITE_SCOPES))


def a1(c):
    return f"'{c['tab']}'!{c['cell']}"


def main(argv):
    approved = "--approved" in argv
    args = [a for a in argv if not a.startswith("--")]
    undo = "--undo" in argv
    if not args:
        sys.exit(__doc__)
    with open(args[0], encoding="utf-8") as f:
        changes = json.load(f)
    if undo:
        changes = [{"tab": c["tab"], "cell": c["cell"], "value": c["old"]} for c in changes]
    sid = gsheet.sheet_id()
    s = session()
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values"
    r = s.get(base + ":batchGet", params={"ranges": [a1(c) for c in changes], "valueRenderOption": "FORMATTED_VALUE"}, timeout=60)
    r.raise_for_status()
    olds = [((vr.get("values") or [[""]])[0] or [""])[0] for vr in r.json().get("valueRanges", [])]
    for i, (c, old) in enumerate(zip(changes, olds), 1):
        c["old"] = old
        print(f"{i}- {c['tab']} {c['cell']}: {old or '(ריק)'} -> {c['value'] or '(ריק)'}")
    if not approved:
        print("(תצוגה בלבד — לא נכתב. כתיבה רק אחרי אישור יקיר: --approved)")
        return 0
    os.makedirs(BACKUPS, exist_ok=True)
    bk = os.path.join(BACKUPS, f"write_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    with open(bk, "w", encoding="utf-8") as f:
        json.dump(changes, f, ensure_ascii=False, indent=1)
    body = {"valueInputOption": "USER_ENTERED", "data": [{"range": a1(c), "values": [[c["value"]]]} for c in changes]}
    w = s.post(base + ":batchUpdate", json=body, timeout=60)
    w.raise_for_status()
    with open(os.path.join(ROOT, "agent", "actions.log"), "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} | Claude | gsheet write{' (undo)' if undo else ''} | "
                + "; ".join(f"{c['tab']}!{c['cell']}: {c['old']!r}->{c['value']!r}" for c in changes) + f" | backup={bk}\n")
    print(f"✔ נכתבו {w.json().get('totalUpdatedCells', 0)} תאים. גיבוי לביטול: {bk}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
