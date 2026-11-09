"""Read-only access to the Segment D Google Sheet (owner rule 09.11.2026: never write without a numbered summary + explicit approval).

Usage (CLI):
  uv run -q --no-project --with google-auth --with requests python -I -X utf8 tools/gsheet.py tabs
  ... gsheet.py values "<tab>" [A1:Z50]        -> JSON rows to stdout
  ... gsheet.py dump <out.json>                 -> every tab, all values
  ... gsheet.py export <out.xlsx>               -> full xlsx copy (backup)
  ... gsheet.py revisions | activity [N]        -> who edited when
Import: from gsheet import Sheet; s = Sheet(); s.values("משכורות")

Only read-only OAuth scopes are requested, so this module cannot modify the sheet even by mistake.
The spreadsheet id is in config (agent/config.json "gsheet_id") or the default below; the sheet name changes monthly.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = os.path.join(ROOT, "secrets", "google-sa.json")
DEFAULT_ID = "1OmyUU7W87L3vagVZHk-30_3h9NxjvUa4ZmwTJUWG2lU"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/drive.activity.readonly"]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheet_id():
    try:
        with open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8") as f:
            return json.load(f).get("gsheet_id") or DEFAULT_ID
    except Exception:
        return DEFAULT_ID


class Sheet:
    def __init__(self, sid=None):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        with open(KEY, encoding="utf-8") as f:
            info = json.load(f)
        self.account = info.get("client_email")
        self.s = AuthorizedSession(service_account.Credentials.from_service_account_info(info, scopes=SCOPES))
        self.id = sid or sheet_id()

    def _get(self, url, **params):
        r = self.s.get(url, params=params, timeout=60)
        r.raise_for_status()
        return r

    def meta(self):
        return self._get(f"https://sheets.googleapis.com/v4/spreadsheets/{self.id}",
                         fields="properties.title,sheets.properties(sheetId,title,gridProperties)").json()

    def tabs(self):
        return [sh["properties"]["title"] for sh in self.meta()["sheets"]]

    def values(self, tab, rng=None):
        a1 = f"'{tab}'" + (f"!{rng}" if rng else "")
        return self._get(f"https://sheets.googleapis.com/v4/spreadsheets/{self.id}/values/{a1}",
                         valueRenderOption="FORMATTED_VALUE").json().get("values", [])

    def dump(self):
        m = self.meta()
        out = {"title": m["properties"]["title"], "tabs": {}}
        for sh in m["sheets"]:
            t = sh["properties"]["title"]
            out["tabs"][t] = self.values(t)
        return out

    def file_info(self):
        return self._get(f"https://www.googleapis.com/drive/v3/files/{self.id}",
                         fields="name,modifiedTime,lastModifyingUser(displayName,emailAddress)").json()

    def export_xlsx(self):
        return self._get(f"https://www.googleapis.com/drive/v3/files/{self.id}/export", mimeType=XLSX).content

    def revisions(self):
        return self._get(f"https://www.googleapis.com/drive/v3/files/{self.id}/revisions", pageSize=200,
                         fields="revisions(id,modifiedTime,lastModifyingUser(displayName,emailAddress))").json().get("revisions", [])

    def activity(self, n=20):
        r = self.s.post("https://driveactivity.googleapis.com/v2/activity:query",
                        json={"itemName": f"items/{self.id}", "pageSize": n}, timeout=60)
        r.raise_for_status()
        return r.json().get("activities", [])


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    s, cmd = Sheet(), argv[0]
    if cmd == "tabs":
        print(json.dumps(s.tabs(), ensure_ascii=False))
    elif cmd == "values":
        print(json.dumps(s.values(argv[1], argv[2] if len(argv) > 2 else None), ensure_ascii=False))
    elif cmd == "dump":
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(s.dump(), f, ensure_ascii=False)
        print(argv[1])
    elif cmd == "export":
        with open(argv[1], "wb") as f:
            f.write(s.export_xlsx())
        print(argv[1])
    elif cmd == "info":
        print(json.dumps(s.file_info(), ensure_ascii=False))
    elif cmd == "revisions":
        print(json.dumps(s.revisions(), ensure_ascii=False, indent=1))
    elif cmd == "activity":
        print(json.dumps(s.activity(int(argv[1]) if len(argv) > 1 else 20), ensure_ascii=False, indent=1))
    else:
        print("unknown command", cmd)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
