"""Full self-test of the YB AI Bridge for WhatsApp. Prints a Hebrew report (✔ / ✖ per stage).

  uv run python -X utf8 tools/selftest.py          # all checks (sends a few test messages to the control group)
  uv run python -X utf8 tools/selftest.py --quiet  # no messages sent
"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
STORE = os.path.expanduser(CFG["store_dir"])
STORE = STORE if os.path.isabs(STORE) else os.path.join(ROOT, STORE)
API = CFG["bridge_url"]
TOKEN = open(os.path.join(STORE, "api_token")).read().strip()
GROUP = CFG["claude_group"]
QUIET = "--quiet" in sys.argv
results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    results.append((ok, name, detail))
    print(("✔" if ok else "✖"), name, "—", detail, flush=True)
    return ok


def call(path, body=None, auth=True, timeout=60):
    h = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def db(name):
    return sqlite3.connect(f"file:{os.path.join(STORE, name)}?mode=ro", uri=True, timeout=10)


def adb():
    return sqlite3.connect(f"file:{os.path.join(ROOT, 'agent', 'agent.db')}?mode=ro", uri=True, timeout=10)


# ---------- bridge ----------
def t_status():
    code, s = call("/status")
    return code == 200 and s.get("connected") and s.get("logged_in"), f"מחובר מאז {s.get('connected_since', '?')[:16]}, ניתוקים {s.get('disconnects')}"


def t_auth():
    code, _ = call("/status", auth=False)
    return code == 401, f"בלי סיסמה התקבל {code} (צריך 401)"


def t_events():
    req = urllib.request.Request(API + "/events", headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=10) as r:
        first = r.readline().decode()
    return first.startswith(": connected"), "זרם האירועים פתוח"


def t_schema():
    con = db("messages.db")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"messages", "chats", "polls", "poll_votes", "send_log", "drafts", "conn_log"}
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    missing = (need - tables) | ({"edited", "deleted"} - cols)
    return not missing and mode == "wal", f"מצב {mode}" + (f", חסר: {missing}" if missing else ", כל הטבלאות קיימות")


def t_lid():
    n = db("messages.db").execute("SELECT count(*) FROM messages WHERE chat_jid LIKE '%@lid'").fetchone()[0]
    mapped = sqlite3.connect(f"file:{os.path.join(STORE, 'whatsapp.db')}?mode=ro", uri=True).execute(
        "SELECT count(*) FROM whatsmeow_lid_map").fetchone()[0]
    return n < 200, f"{n} הודעות עדיין תחת מזהה LID (אנשים בלי מספר ידוע), {mapped} מיפויים"


def t_media_names():
    rows = db("messages.db").execute("SELECT id, filename FROM messages WHERE media_type != '' AND media_type != 'poll' "
                                    "ORDER BY rowid DESC LIMIT 20").fetchall()
    new = [r for r in rows if r[0] in (r[1] or "")]
    return True, f"{len(new)} מתוך 20 קבצי המדיה האחרונים כבר בשם הייחודי (החדשים)"


def t_policy():
    code, p = call("/policy")
    return code == 200 and p.get("mode") == "draft", "מצב שליחה: טיוטות בלבד" if p.get("mode") == "draft" else f"מצב שליחה לא צפוי: {p.get('mode')}"


def t_send_owner():
    code, r = call("/send", {"recipient": GROUP, "message": "🤖 🧪 בדיקה עצמית: שליחה לקבוצה"})
    mid = r.get("message", "").split("id=")[-1]
    time.sleep(2)
    stored = db("messages.db").execute("SELECT count(*) FROM messages WHERE id = ?", (mid,)).fetchone()[0]
    return r.get("success") and stored == 1, f"נשלחה ונשמרה (מזהה {mid})"


def t_draft():
    code, r = call("/send", {"recipient": "972500000000", "message": "🧪 בדיקה עצמית — טיוטה למספר בדיקה, לא נשלחת"})
    ok = not r.get("success") and "DRAFT" in r.get("message", "")
    row = db("messages.db").execute("SELECT id FROM drafts WHERE recipient = '972500000000@s.whatsapp.net' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        con = sqlite3.connect(os.path.join(STORE, "messages.db"), timeout=10)
        con.execute("UPDATE drafts SET status = 'discarded' WHERE id = ?", row)
        con.commit()
    return ok, "לא נשלחה — נוצרה טיוטה (ובוטלה בסוף הבדיקה)"


def t_quote_react():
    last = db("messages.db").execute("SELECT id FROM messages WHERE chat_jid = ? AND is_from_me = 1 AND content NOT LIKE '🤖%' "
                                     "ORDER BY rowid DESC LIMIT 1", (GROUP,)).fetchone()
    if not last:
        return False, "אין הודעה שלך בקבוצה לצטט"
    _, r1 = call("/send", {"recipient": GROUP, "message": "🤖 🧪 בדיקה עצמית: תגובה עם ציטוט", "quoted_id": last[0]})
    _, r2 = call("/react", {"chat_jid": GROUP, "message_id": last[0], "sender": "", "emoji": "🧪"})
    call("/react", {"chat_jid": GROUP, "message_id": last[0], "sender": "", "emoji": ""})
    _, r3 = call("/typing", {"chat_jid": GROUP, "on": False})
    return r1.get("success") and r2.get("success") and r3.get("success"), "ציטוט, תגובת אימוג'י ו'מקליד' עובדים"


def t_poll_tables():
    con = db("messages.db")
    return True, f"{con.execute('SELECT count(*) FROM polls').fetchone()[0]} סקרים, {con.execute('SELECT count(*) FROM poll_votes').fetchone()[0]} הצבעות נשמרו"


# ---------- agent ----------
def kv(k):
    r = adb().execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
    return r[0] if r else None


def t_agent_alive():
    hb = kv("heartbeat")
    age = (dt.datetime.now() - dt.datetime.fromisoformat(hb)).total_seconds() if hb else 1e9
    return age < 120, f"דופק לפני {int(age)} שניות"


def t_agent_events():
    ev = kv("events_connected")
    return bool(ev), f"מחובר לזרם האירועים מאז {ev[:16] if ev else '—'}"


def t_plugins():
    log = open(os.path.join(ROOT, "agent", "agent.log"), encoding="utf-8").read().splitlines()
    starts = [i for i, l in enumerate(log) if "agent starting" in l]
    last_start = starts[-1] if starts else len(log)
    prev = starts[-2] if len(starts) > 1 else -1
    window = log[prev + 1:last_start + 1]  # plugins are loaded just before "agent starting"
    loaded = [l for l in window if "plugin loaded" in l]
    failed = [l for l in window if "plugin failed" in l]
    return len(loaded) == len(CFG.get("plugins", [])) and not failed, f"{len(loaded)} תוספים נטענו"


def t_transcripts():
    n = adb().execute("SELECT count(*) FROM transcripts").fetchone()[0]
    return n > 0, f"{n} הודעות קוליות תומללו עד עכשיו"


def t_e2e():
    code, r = call("/send", {"recipient": GROUP, "message": "🧪 בדיקה עצמית של הסוכן: ענה במילה אחת — תקין"})
    mid = r.get("message", "").split("id=")[-1]
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(5)
        row = adb().execute("SELECT status FROM commands WHERE msg_id = ?", (mid,)).fetchone()
        if row and row[0] == "done":
            ans = db("messages.db").execute("SELECT content FROM messages WHERE chat_jid = ? AND content LIKE '🤖%' "
                                            "ORDER BY rowid DESC LIMIT 1", (GROUP,)).fetchone()
            return True, f"הסוכן ענה תוך {int(time.time() - t0)} שניות: {ans[0][:40] if ans else ''}"
    return False, "הסוכן לא ענה תוך 3 דקות"


def t_backup():
    root = os.path.join(os.path.expanduser(CFG["backup_dir"]), "db")
    days = sorted(os.listdir(root)) if os.path.isdir(root) else []
    return bool(days), f"גיבוי אחרון: {days[-1] if days else 'אין'}"


# ---------- dashboard / MCP / tasks ----------
def t_dash():
    with urllib.request.urlopen("http://127.0.0.1:8770/data", timeout=20) as r:
        d = json.loads(r.read().decode())
    with urllib.request.urlopen("http://127.0.0.1:8770/", timeout=10) as r:
        html = r.read().decode()
    return "version" in d and "ווצאפ" in html, f"לוח הבקרה עונה, גרסה {d.get('version')}"


def t_mcp():
    code = "import whatsapp; c = whatsapp.list_chats(limit=3); print(len(c), bool(whatsapp._auth()))"
    import shutil
    out = subprocess.run([shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe"), "run", "-q", "--directory", os.path.join(ROOT, "whatsapp-mcp-server"),
                          "python", "-c", code], capture_output=True, text=True, encoding="utf-8").stdout.split()
    return len(out) == 2 and int(out[0]) > 0 and out[1] == "True", f"כלי ה-MCP קורא שיחות ({out[0] if out else 0}) ושולח עם סיסמה"


def t_tasks():
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          "(Get-ScheduledTask -TaskName 'WhatsApp*' | Select -Expand TaskName) -join ','"], capture_output=True, text=True).stdout.strip()
    return "WhatsApp bridge" in out and "WhatsApp agent" in out, f"משימות הפעלה: {out}"


def t_exe():
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "WhatsAppDash", "WhatsAppDash.exe")
    return os.path.exists(p), "אפליקציית לוח הבקרה מותקנת" if os.path.exists(p) else "האפליקציה לא מותקנת"


if __name__ == "__main__":
    print("🧪 בדיקה עצמית — YB גשר AI לווצאפ\n")
    print("— גשר —")
    check("חיבור לווצאפ", t_status)
    check("הגנת סיסמה", t_auth)
    check("זרם אירועים מיידי", t_events)
    check("מסד נתונים", t_schema)
    check("איחוד אנשי קשר", t_lid)
    check("שמות קבצי מדיה", t_media_names)
    check("סקרים", t_poll_tables)
    check("מדיניות שליחה", t_policy)
    if not QUIET:
        check("שליחה אליך", t_send_owner)
        check("מצב טיוטה", t_draft)
        check("ציטוט / אימוג'י / מקליד", t_quote_react)
    print("\n— סוכן —")
    check("הסוכן פעיל", t_agent_alive)
    check("עדכון מיידי בסוכן", t_agent_events)
    check("תוספים", t_plugins)
    check("תמלול", t_transcripts)
    check("גיבוי", t_backup)
    if not QUIET:
        check("שאלה ותשובה מקצה לקצה", t_e2e)
    print("\n— לוח בקרה, MCP, הפעלה —")
    check("לוח הבקרה", t_dash)
    check("כלי ה-MCP", t_mcp)
    check("הפעלה אוטומטית", t_tasks)
    check("אפליקציה (EXE)", t_exe)
    ok = sum(1 for r in results if r[0])
    print(f"\nסיכום: {ok} מתוך {len(results)} תקינים")
    json.dump([{"ok": o, "name": n, "detail": d} for o, n, d in results],
              open(os.path.join(ROOT, "agent", "selftest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
