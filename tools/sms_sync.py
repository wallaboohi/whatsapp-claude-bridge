"""SMS bridge (owner 17.11): reads the phone's SMS inbox over adb (read-only), keeps METADATA ONLY in agent/sms.db
(sender, time, read flag, class, reason, status) - the message text is classified in memory and thrown away.
Nothing is sent, edited or deleted by this tool; any future send/edit/delete goes through a draft + owner approval.

Usage (from the tool root):
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py sync      new messages since the last run (agent runs it every 5 min)
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py summary   JSON digest: counts by class, last important ones
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py index [N]          table of contents: one row per sender (count, unread, last, classes)
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py search <words> [--limit N]   full-text search (snippets, metadata)
  uv run -q --no-project python -I -X utf8 tools/sms_sync.py show <id>        full local copy of one SMS (owner reading only)
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
    # Owner 17.11 13:50: fast retrieval. Full-text index over the local body + sender (rowid = sms.id).
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS sms_fts USING fts5(body, sender, tokenize='unicode61')")
    c.execute("CREATE INDEX IF NOT EXISTS sms_sender_ts ON sms(sender, ts)")
    c.execute("INSERT INTO sms_fts(rowid, body, sender) SELECT id, body, sender FROM sms "
              "WHERE id NOT IN (SELECT rowid FROM sms_fts)")  # backfill (no-op once everything is indexed)
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
        cur = c.execute("INSERT OR IGNORE INTO sms(id,sender,ts,read,cls,reason,body) VALUES(?,?,?,?,?,?,?)", (_id, addr, ts, read, cls, why, body))
        if cur.rowcount:
            c.execute("INSERT INTO sms_fts(rowid, body, sender) VALUES(?,?,?)", (_id, body, addr))
        n += 1
    c.commit()
    return {"new": n, "last_id": c.execute("SELECT MAX(id) FROM sms").fetchone()[0]}


def summary():
    c = db()
    counts = dict(c.execute("SELECT cls, COUNT(*) FROM sms WHERE ts>=date('now','-7 day') GROUP BY cls"))
    imp = [dict(zip(("sender", "ts", "reason", "read"), r)) for r in c.execute(
        "SELECT sender, ts, reason, read FROM sms WHERE cls='important' ORDER BY id DESC LIMIT 10")]
    return {"last_7_days": counts, "recent_important": imp}


def index(limit=40):
    """Table of contents: one row per sender (count, unread, last time, class mix, last reason). Metadata only."""
    c = db()
    rows = c.execute("""SELECT sender, COUNT(*), SUM(CASE WHEN read=0 THEN 1 ELSE 0 END), MAX(ts),
                        SUM(cls='important'), SUM(cls='spam'), SUM(cls='personal'), MAX(id)
                        FROM sms GROUP BY sender ORDER BY MAX(id) DESC LIMIT ?""", (int(limit),)).fetchall()
    senders = [{"sender": r[0], "count": r[1], "unread": r[2] or 0, "last": r[3], "important": r[4] or 0,
                "spam": r[5] or 0, "personal": r[6] or 0} for r in rows]
    total = c.execute("SELECT COUNT(*) FROM sms").fetchone()[0]
    return {"total": total, "senders_shown": len(senders), "senders": senders}


def _fts_query(q):
    toks = [t for t in re.split(r"\s+", q.strip()) if t]
    return " ".join('"' + t.replace('"', '""') + '"*' for t in toks)


def search(q, limit=20):
    """Full-text search over the local bodies. Returns metadata plus a short snippet (not the full text)."""
    c = db()
    fq = _fts_query(q)
    if not fq:
        return {"results": []}
    rows = c.execute("""SELECT s.id, s.sender, s.ts, s.read, s.cls, s.reason,
                        snippet(sms_fts, 0, '[', ']', '…', 12) FROM sms_fts
                        JOIN sms s ON s.id = sms_fts.rowid WHERE sms_fts MATCH ? ORDER BY s.id DESC LIMIT ?""",
                     (fq, int(limit))).fetchall()
    return {"results": [dict(zip(("id", "sender", "ts", "read", "cls", "reason", "snippet"), r)) for r in rows]}


def show(i):
    """Full local copy of one SMS (for the owner's own reading; never sent anywhere by this tool)."""
    c = db()
    r = c.execute("SELECT id, sender, ts, read, cls, reason, body FROM sms WHERE id=?", (int(i),)).fetchone()
    if not r:
        return {"error": "not found"}
    return dict(zip(("id", "sender", "ts", "read", "cls", "reason", "body"), r))


def delete(ids):
    """Delete from the phone AND the local copy. Only with the owner's explicit approval (caller passes --approved)."""
    c = db()
    res = {}
    for i in ids:
        rc, out = phone_sync.adb("shell", f"content delete --uri content://sms/{int(i)}", timeout=60)
        gone = not fetch_one(i)
        if gone:
            c.execute("DELETE FROM sms WHERE id=?", (int(i),))
            c.execute("DELETE FROM sms_fts WHERE rowid=?", (int(i),))
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
    if cmd == "index":
        out = index(int(sys.argv[2]) if len(sys.argv) > 2 else 40)
    elif cmd == "search":
        args = sys.argv[2:]
        lim = int(args[args.index("--limit") + 1]) if "--limit" in args else 20
        words = [a for i, a in enumerate(args) if a != "--limit" and not (i and args[i - 1] == "--limit")]
        out = search(" ".join(words), lim)
    elif cmd == "show":
        out = show(sys.argv[2])
    else:
        out = sync() if cmd == "sync" else summary()
    print(json.dumps(out, ensure_ascii=False, indent=1))
