"""Owner 19.11.2026 21:14: "בנה מנגנון בגשר לנושאים שאני מעלה ולא מתממשים בקוד מכל סיבה".

Every request the owner writes in a control group is kept (agent.db, table owner_asks) until it is closed:
  - automatically, when a commit in the bridge / ppe-tracker repo cites the minute of the message ("owner 19.11 21:14" or
    "יקיר 19.11 21:14") -> status "coded" with the commit hash;
  - by the AI that did the work: `python tools/asks.py done <id> "<what was done / commit>"`, or `not <id> "<why not>"`;
  - by the owner: /פתוחים lists what is still open; /פתוחים סגור <id> closes one.
Open asks are given to the AI with every new message of the owner (short block), so nothing he raised is silently lost.

Pure stdlib (sqlite3 + git through subprocess); the agent and the CLI tool share it."""
import datetime as dt
import os
import re
import sqlite3
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "agent", "agent.db")
REPOS = (ROOT, os.path.join(os.path.dirname(ROOT), "ppe-tracker"))
STATUS_HE = {"open": "פתוח", "coded": "בקוד", "done": "בוצע", "not": "לא יבוצע", "dup": "כפול"}

# Not a request: acks, emoji only, numbers-only replies to a numbered list are kept (they carry requests), slash commands not.
_ACK = re.compile(r"^\s*(אוקי|אוקיי|ok|תודה|סבבה|מעולה|יופי|כן|לא|טוב|👍|🙏|✅|❌|\W)\s*[.!]*\s*$", re.I)


def _db(path=None):
    db = sqlite3.connect(path or DB, timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS owner_asks (id INTEGER PRIMARY KEY, at TEXT, chat TEXT, mid TEXT, text TEXT,
                  status TEXT DEFAULT 'open', ref TEXT DEFAULT '', note TEXT DEFAULT '', closed_at TEXT DEFAULT '')""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS owner_asks_mid ON owner_asks(chat, mid)")
    return db


def is_request(text):
    t = (text or "").strip()
    if not t or t.lstrip("!").lstrip().startswith("/") or _ACK.match(t):
        return False
    return len(t) >= 12


def record(chat, mid, when, text, path=None):
    """Keep one owner message as an ask (idempotent per message id). Returns its id or None."""
    if not is_request(text):
        return None
    at = str(when or dt.datetime.now().isoformat(timespec="seconds"))[:19].replace("T", " ")
    with _db(path) as db:
        db.execute("INSERT OR IGNORE INTO owner_asks (at, chat, mid, text) VALUES (?, ?, ?, ?)", (at, chat or "", str(mid or at), text.strip()))
        row = db.execute("SELECT id FROM owner_asks WHERE chat = ? AND mid = ?", (chat or "", str(mid or at))).fetchone()
    return row[0] if row else None


def close(ask_id, status="done", note="", ref="", path=None):
    if status not in STATUS_HE:
        raise ValueError(status)
    with _db(path) as db:
        n = db.execute("UPDATE owner_asks SET status = ?, note = ?, ref = COALESCE(NULLIF(?, ''), ref), closed_at = ? WHERE id = ?",
                       (status, note, ref, "" if status == "open" else dt.datetime.now().isoformat(timespec="seconds"), int(ask_id))).rowcount
    return bool(n)


def open_asks(chat=None, days=14, path=None):
    cut = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    q = "SELECT id, at, chat, text, status, ref, note FROM owner_asks WHERE status = 'open' AND at >= ?"
    args = [cut]
    if chat:
        q += " AND chat = ?"
        args.append(chat)
    with _db(path) as db:
        return db.execute(q + " ORDER BY at", args).fetchall()


_CITE = re.compile(r"(?:owner|יקיר|Owner)\s+(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\s+(\d{1,2}):(\d{2})")


def sync_commits(days=10, path=None, repos=REPOS):
    """Close asks whose minute is cited by a commit (subject or body) as "coded". Returns how many were closed."""
    cites = {}
    for repo in repos:
        if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(os.path.join(repo, ".git")):
            continue
        try:
            out = subprocess.run(["git", "-C", repo, "log", f"--since={days}.days", "--format=%h%x1f%s%n%b%x1e"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for rec in out.split("\x1e"):
            if "\x1f" not in rec:
                continue
            h, msg = rec.strip().split("\x1f", 1)
            for d, m, hh, mm in _CITE.findall(msg):
                cites.setdefault((int(d), int(m), int(hh), int(mm)), h)
    if not cites:
        return 0
    n = 0
    with _db(path) as db:
        for i, at in db.execute("SELECT id, at FROM owner_asks WHERE status = 'open'").fetchall():
            try:
                t = dt.datetime.fromisoformat(at)
            except ValueError:
                continue
            h = cites.get((t.day, t.month, t.hour, t.minute))
            if h:
                n += db.execute("UPDATE owner_asks SET status = 'coded', ref = ?, closed_at = ? WHERE id = ?",
                                (h, dt.datetime.now().isoformat(timespec="seconds"), i)).rowcount
    return n


def _short(text, n=110):
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def prompt_block(chat=None, limit=8, path=None, skip_id=None):
    """The block added to the AI's prompt: what the owner raised and is still open (oldest first)."""
    rows = [r for r in open_asks(chat, path=path) if r[0] != skip_id][-limit:]
    if not rows:
        return ""
    tool = "uv run -q --no-project python -I -X utf8 C:/Users/walla/whatsapp-mcp/tools/asks.py"
    lines = [f"#{i} ({at[8:10]}.{at[5:7]} {at[11:16]}) \"{_short(t)}\"" for i, at, _c, t, *_ in rows]
    return ("בקשות של יקיר שעדיין פתוחות (מנגנון הנושאים הפתוחים, לא הוראה חדשה; אל תתחיל אותן בלי שביקש עכשיו). "
            f"כשסיימת אחת: `{tool} done <id> \"<מה נעשה + קומיט>\"`; אם לא תבוצע: `{tool} not <id> \"<למה>\"`. "
            "קומיט שמצטט את שעת ההודעה (owner DD.MM HH:MM) סוגר אותה לבד.\n" + "\n".join(lines))


def report(chat=None, path=None):
    """/פתוחים: Hebrew list of what is still open, oldest first, plus what was closed in the last day."""
    try:
        sync_commits(path=path)
    except Exception:
        pass
    rows = open_asks(chat, path=path)
    with _db(path) as db:
        cut = (dt.datetime.now() - dt.timedelta(days=1)).isoformat(timespec="seconds")
        q = "SELECT id, at, text, status, ref, note FROM owner_asks WHERE status != 'open' AND closed_at >= ?" + (" AND chat = ?" if chat else "")
        closed = db.execute(q + " ORDER BY closed_at", [cut] + ([chat] if chat else [])).fetchall()
    out = [f"📌 *נושאים שהעלית ועדיין לא מומשו:* {len(rows)}"]
    now = dt.datetime.now()
    for i, at, _c, t, *_ in rows:
        try:
            age = now - dt.datetime.fromisoformat(at)
            ago = f"{age.days} ימים" if age.days else f"{age.seconds // 3600} שעות"
        except ValueError:
            ago = ""
        out.append(f"• #{i} ({at[8:10]}.{at[5:7]} {at[11:16]}, לפני {ago}) _\"{_short(t, 140)}\"_")
    if closed:
        out += ["", "✅ *נסגרו ב-24 השעות האחרונות:*"]
        out += [f"• #{i} {STATUS_HE.get(st, st)}{(' · ' + ref) if ref else ''}{(' · ' + _short(note, 80)) if note else ''}: _\"{_short(t, 80)}\"_"
                for i, at, t, st, ref, note in closed]
    out.append("\nסגירה ידנית: /פתוחים סגור <מספר> · ביטול: /פתוחים בטל <מספר>")
    return "\n".join(out)
