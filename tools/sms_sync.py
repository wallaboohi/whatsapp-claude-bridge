"""SMS bridge (owner 17.11): reads the phone's SMS inbox over adb (read-only), keeps METADATA ONLY in agent/sms.db
(sender, time, read flag, class, reason, status) - the message text is classified in memory and thrown away.
Nothing is sent, edited or deleted by this tool; any future send/edit/delete goes through a draft + owner approval.

Usage (from the tool root):
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py sync      new messages since the last run (agent runs it every 5 min)
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py summary   JSON digest: counts by class, last important ones
"""
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_sync  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "sms.db")
IMPORTANT = [("otp", r"\b\d{4,8}\b.*(קוד|code|verification|אימות)|(קוד|code|verification|אימות).*\b\d{4,8}\b"),
             ("bank", r"בנק|אשראי|חיוב|כרטיס|bank|visa|isracard|max\b|cal\b"),
             ("delivery", r"משלוח|חבילה|שליח|דואר|delivery|parcel"),
             ("appointment", r"תור|פגישה|תזכורת|appointment")]
SPAM = [("link+promo", r"https?://\S+.*(מבצע|הנחה|קופון|הטבה|sale|%|הסרה)|(מבצע|הנחה|קופון|הטבה).*https?://"),
        ("unsubscribe", r"הסרה|להסרה|unsubscribe|STOP"),
        ("promo", r"מבצע|הנחה|קופון|הטבה|זכית|הגרלה")]


def classify(sender, body):
    for name, rx in IMPORTANT:
        if re.search(rx, body, re.I | re.S):
            return "important", name
    for name, rx in SPAM:
        if re.search(rx, body, re.I | re.S):
            return "spam", name
    if re.fullmatch(r"\+?\d{9,13}|0\d{8,10}", sender or ""):
        return "personal", "number"
    return "other", "sender-name"


def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS sms (id INTEGER PRIMARY KEY, sender TEXT, ts TEXT, read INTEGER,
                 cls TEXT, reason TEXT, status TEXT DEFAULT 'new')""")
    if "body" not in [r[1] for r in c.execute("PRAGMA table_info(sms)")]:
        c.execute("ALTER TABLE sms ADD COLUMN body TEXT")  # owner 17.11: full content is kept locally
    return c


def fetch(min_id):
    rc, out = phone_sync.adb("shell", f"content query --uri content://sms/inbox --projection _id:address:date:read:body "
                             f"--where '_id>{int(min_id)}' --sort '_id ASC'", timeout=180)
    if rc != 0:
        raise RuntimeError("adb failed: " + out[:200])
    for chunk in re.split(r"(?m)^Row: \d+ ", out):
        m = re.match(r"_id=(\d+), address=(.*?), date=(\d+), read=(\d), body=(.*)", chunk, re.S)
        if m:
            yield int(m[1]), m[2], int(m[3]), int(m[4]), m[5]


def sync():
    c = db()
    last = c.execute("SELECT COALESCE(MAX(id),0) FROM sms").fetchone()[0]
    n = 0
    for _id, addr, ms, read, body in fetch(last):
        cls, why = classify(addr, body)  
        ts = dt.datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
        c.execute("INSERT OR IGNORE INTO sms(id,sender,ts,read,cls,reason,body) VALUES(?,?,?,?,?,?,?)", (_id, addr, ts, read, cls, why, body))
        n += 1
    c.commit()
    return {"new": n, "last_id": c.execute("SELECT MAX(id) FROM sms").fetchone()[0]}


def summary():
    c = db()
    counts = dict(c.execute("SELECT cls, COUNT(*) FROM sms WHERE ts>=date('now','-7 day') GROUP BY cls"))
    imp = [dict(zip(("sender", "ts", "reason", "read"), r)) for r in c.execute(
        "SELECT sender, ts, reason, read FROM sms WHERE cls='important' ORDER BY id DESC LIMIT 10")]
    return {"last_7_days": counts, "recent_important": imp}


def delete(ids):
    """Delete from the phone AND the local copy. Only with the owner's explicit approval (caller passes --approved)."""
    c = db()
    res = {}
    for i in ids:
        rc, out = phone_sync.adb("shell", f"content delete --uri content://sms/{int(i)}", timeout=60)
        gone = not fetch_one(i)
        if gone:
            c.execute("DELETE FROM sms WHERE id=?", (int(i),))
        res[i] = "deleted" if gone else "phone refused: " + out[:120]
    c.commit()
    return res


def fetch_one(i):
    rc, out = phone_sync.adb("shell", f"content query --uri content://sms --projection _id --where '_id={int(i)}'", timeout=60)
    return "_id=" in out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    if cmd == "delete":
        if "--approved" not in sys.argv:
            sys.exit("refusing: deletion needs the owner's explicit approval (--approved)")
        print(json.dumps(delete([a for a in sys.argv[2:] if a.isdigit()]), ensure_ascii=False)); sys.exit()
    print(json.dumps(sync() if cmd == "sync" else summary(), ensure_ascii=False, indent=1))
