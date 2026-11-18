"""Phone-data catalog (owner 19.11): one place where every AI in the bridge finds people and phone content.
Reads the phone over adb with `content query` ONLY (read-only: nothing on the phone is changed, deleted or sent) and keeps
a local copy in agent/phone.db: phone contacts, call log, calendar events, call recordings (+ transcripts), and an
`identities` table that joins per person the phone-contact name, the WhatsApp name / push name, the LID and the owner's
nickname (agent.db `people`, bridge whatsapp.db; both opened read-only).

Usage (from the tool root):
  uv run -q --no-project python -I -X utf8 tools/phone_db.py sync               contacts + calls + calendar (new/changed only), identities
  uv run -q --no-project python -I -X utf8 tools/phone_db.py status             counts, last sync, phone reachable
  uv run -q --no-project python -I -X utf8 tools/phone_db.py who <name|phone|lid|#id>   every identity of a person + last calls + WhatsApp chat
  uv run -q --no-project python -I -X utf8 tools/phone_db.py calls [N] [--who X]        last calls (names first)
  uv run -q --no-project python -I -X utf8 tools/phone_db.py events [--from YYYY-MM-DD] [--to YYYY-MM-DD]
  uv run -q --no-project python -I -X utf8 tools/phone_db.py index [N]          table of contents: one row per person (calls, messages, last contact)
  uv run -q --no-project python -I -X utf8 tools/phone_db.py search <words> [--limit N]  full-text: names, events, recording transcripts
  uv run -q --no-project python -I -X utf8 tools/phone_db.py recordings [--transcribe N]  copy new call recordings, link to calls, transcribe

Output is JSON. Rule for anything shown to the owner: names, never numbers ("איש קשר לא מזוהה" when no name is known).
Numbers / LIDs / JIDs appear in `who` only, for other tools to use.
Config (agent/config.json "phone"): "adb", "data_dir", optional "recording_dirs" (list of phone folders).
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("WA_PHONE_DB") or os.path.join(ROOT, "agent", "phone.db")
AGENT_DB = os.environ.get("WA_AGENT_DB") or os.path.join(ROOT, "agent", "agent.db")
STORE = os.path.join(ROOT, "whatsapp-bridge", "store")
WA_DB, MSG_DB = os.path.join(STORE, "whatsapp.db"), os.path.join(STORE, "messages.db")
REC_DIR = os.path.join(phone_sync.DATA, "call-recordings")
UNKNOWN = "איש קשר לא מזוהה"
REC_DIRS = phone_sync.CFG.get("recording_dirs") or [
    "/sdcard/Recordings/Call recordings", "/sdcard/Recordings/Call", "/sdcard/Recordings",
    "/sdcard/Music/Recordings/Call Recordings", "/sdcard/MIUI/sound_recorder/call_rec",
    "/sdcard/Android/data/com.mediatek.callrecorder/files", "/sdcard/Call", "/sdcard/CallRecordings",
    "/sdcard/PhoneRecord", "/sdcard/Sounds/CallRecord"]
AUDIO = re.compile(r"\.(m4a|amr|3gp|wav|aac|mp3|ogg|opus)$", re.I)
CALL_TYPES = {1: "in", 2: "out", 3: "missed", 4: "voicemail", 5: "rejected", 6: "blocked", 7: "answered_elsewhere"}
MIME = "vnd.android.cursor.item/"
KINDS = ("phone_v2", "nickname", "email_v2", "organization")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS contacts (contact_id INTEGER PRIMARY KEY, display_name TEXT, nicknames TEXT, emails TEXT,
    org TEXT, updated_ms INTEGER);
CREATE TABLE IF NOT EXISTS contact_phones (contact_id INTEGER, phone TEXT, raw TEXT, PRIMARY KEY (contact_id, phone));
CREATE INDEX IF NOT EXISTS contact_phones_phone ON contact_phones(phone);
CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, phone TEXT, raw_number TEXT, name TEXT, type TEXT,
    ts TEXT, ms INTEGER, duration INTEGER, geo TEXT);
CREATE INDEX IF NOT EXISTS calls_phone_ms ON calls(phone, ms);
CREATE TABLE IF NOT EXISTS calendars (id INTEGER PRIMARY KEY, name TEXT, account TEXT, visible INTEGER);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, calendar_id INTEGER, title TEXT, start TEXT, end TEXT,
    all_day INTEGER, location TEXT, rrule TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS event_instances (event_id INTEGER, begin TEXT, end TEXT, PRIMARY KEY (event_id, begin));
CREATE TABLE IF NOT EXISTS recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, phone_path TEXT UNIQUE, local_path TEXT,
    size INTEGER, mtime INTEGER, call_id INTEGER, phone TEXT, transcript TEXT, status TEXT DEFAULT 'new');
CREATE TABLE IF NOT EXISTS identities (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, phone TEXT, lid TEXT,
    contact_id INTEGER, contact_name TEXT, wa_name TEXT, push_name TEXT, nickname TEXT, call_name TEXT, name TEXT,
    updated_at TEXT);
CREATE INDEX IF NOT EXISTS identities_phone ON identities(phone);
CREATE INDEX IF NOT EXISTS identities_lid ON identities(lid);
CREATE VIRTUAL TABLE IF NOT EXISTS phone_fts USING fts5(kind UNINDEXED, ref UNINDEXED, title, body, tokenize='unicode61');
"""


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def meta(c, k, v=None):
    if v is None:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None
    c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))


def norm(num):
    """Phone number -> digits in 972XXXXXXXXX form for Israeli numbers; other numbers digits only; '' for none."""
    s = str(num or "").strip()
    if not s or s.startswith("*") or s in ("-1", "-2", "-3"):
        return re.sub(r"\D", "", s)
    d = re.sub(r"\D", "", s)
    if d.startswith("00"):
        d = d[2:]
    elif d.startswith("0") and len(d) in (9, 10):
        d = "972" + d[1:]
    elif len(d) == 9 and d[0] == "5":  # Israeli mobile without the trunk 0 (the call log's normalized_number)
        d = "972" + d
    if d.startswith("9720"):
        d = "972" + d[4:]
    return d


def ms_iso(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000).isoformat(timespec="seconds") if ms not in (None, "", "NULL") else None


# ---------- adb `content query` ----------

def query(uri, cols, where=None, sort=None, timeout=300):
    """Rows of a content provider as dicts. Values may contain ', ' so a row is split only before a known column name."""
    cmd = f"content query --uri {uri} --projection {':'.join(cols)}"
    if where:
        cmd += " --where '" + where.replace("'", "'\\''") + "'"
    if sort:
        cmd += f" --sort '{sort}'"
    rc, out = phone_sync.adb("shell", cmd, timeout=timeout)
    if rc != 0 and not out.strip() and phone_sync.connect():  # a dropped adb-over-network session: one retry
        rc, out = phone_sync.adb("shell", cmd, timeout=timeout)
    if rc != 0 or out.lstrip().startswith("Error"):
        raise RuntimeError(f"adb query {uri} failed: " + out.strip()[:200])
    split = re.compile(r", (?=(?:" + "|".join(map(re.escape, cols)) + r")=)")
    rows = []
    for chunk in re.split(r"(?m)^Row: \d+ ", out)[1:]:
        r = {}
        for part in split.split(chunk.rstrip("\r\n")):
            k, _, v = part.partition("=")
            r[k] = None if v == "NULL" else v
        rows.append(r)
    return rows


# ---------- sync ----------

def sync_contacts(c):
    """Changed contacts only (contact_last_updated_timestamp), plus deletions from deleted_contacts."""
    since = int(meta(c, "contacts_ms") or 0)
    changed = query("content://com.android.contacts/contacts", ["_id", "display_name", "contact_last_updated_timestamp"],
                    where=f"contact_last_updated_timestamp>{since}")
    ids = [int(r["_id"]) for r in changed]
    data = {}
    for i in range(0, len(ids), 400):
        batch = ",".join(map(str, ids[i:i + 400]))
        kinds = ",".join(f'"{MIME}{k}"' for k in KINDS)
        for r in query("content://com.android.contacts/data", ["contact_id", "mimetype", "data1", "data4"],
                       where=f"contact_id IN ({batch}) AND mimetype IN ({kinds})"):
            data.setdefault(int(r["contact_id"]), []).append(r)
    top = since
    for r in changed:
        cid = int(r["_id"])
        top = max(top, int(r["contact_last_updated_timestamp"] or 0))
        phones, nicks, emails, org = {}, [], [], []
        for d in data.get(cid, []):
            kind, v1, v4 = d["mimetype"].replace(MIME, ""), d.get("data1"), d.get("data4")
            if kind == "phone_v2" and (v1 or v4):
                n = norm(v4 or v1)
                if n:
                    phones[n] = v1
            elif kind == "nickname" and v1:
                nicks.append(v1)
            elif kind == "email_v2" and v1:
                emails.append(v1)
            elif kind == "organization" and (v1 or v4):
                org.append(" - ".join(x for x in (v1, v4) if x))
        c.execute("INSERT OR REPLACE INTO contacts VALUES (?,?,?,?,?,?)",
                  (cid, r.get("display_name"), "; ".join(dict.fromkeys(nicks)) or None,
                   "; ".join(dict.fromkeys(emails)) or None, "; ".join(dict.fromkeys(org)) or None,
                   int(r["contact_last_updated_timestamp"] or 0)))
        c.execute("DELETE FROM contact_phones WHERE contact_id=?", (cid,))
        c.executemany("INSERT OR IGNORE INTO contact_phones VALUES (?,?,?)", [(cid, p, raw) for p, raw in phones.items()])
        extra = " ".join(filter(None, [*nicks, *emails, *org]))  # the name itself is indexed with the person
        _fts(c, "contact", cid, r.get("display_name") if extra else None, extra)
    gone = 0
    dsince = int(meta(c, "contacts_deleted_ms") or 0)
    dtop = dsince
    for r in query("content://com.android.contacts/deleted_contacts", ["contact_id", "contact_deleted_timestamp"],
                   where=f"contact_deleted_timestamp>{dsince}"):
        cid = int(r["contact_id"])
        dtop = max(dtop, int(r["contact_deleted_timestamp"] or 0))
        gone += c.execute("DELETE FROM contacts WHERE contact_id=?", (cid,)).rowcount
        c.execute("DELETE FROM contact_phones WHERE contact_id=?", (cid,))
        _fts(c, "contact", cid, None, None)
    meta(c, "contacts_ms", top)
    meta(c, "contacts_deleted_ms", dtop)
    return {"changed": len(changed), "deleted": gone}


def sync_calls(c):
    last = c.execute("SELECT COALESCE(MAX(id),0) FROM calls").fetchone()[0]
    rows = query("content://call_log/calls", ["_id", "number", "normalized_number", "name", "type", "date", "duration",
                                              "geocoded_location"], where=f"_id>{int(last)}", sort="_id ASC")
    for r in rows:
        c.execute("INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?,?,?,?,?)",
                  (int(r["_id"]), norm(r.get("number") or r.get("normalized_number")), r.get("number"), r.get("name") or None,
                   CALL_TYPES.get(int(r.get("type") or 0), str(r.get("type"))), ms_iso(r["date"]), int(r["date"] or 0),
                   int(r.get("duration") or 0), r.get("geocoded_location") or None))
    return {"new": len(rows)}


def sync_calendar(c):
    """Small (~100 events): fully refreshed each sync. Recurring events are expanded via instances for -90..+365 days."""
    cals = query("content://com.android.calendar/calendars", ["_id", "calendar_displayName", "account_name", "visible"])
    c.execute("DELETE FROM calendars")
    c.executemany("INSERT INTO calendars VALUES (?,?,?,?)",
                  [(int(r["_id"]), r.get("calendar_displayName"), r.get("account_name"), int(r.get("visible") or 0)) for r in cals])
    evs = query("content://com.android.calendar/events", ["_id", "calendar_id", "title", "dtstart", "dtend", "allDay",
                                                          "eventLocation", "rrule", "deleted", "description"])
    c.execute("DELETE FROM events")
    c.execute("DELETE FROM phone_fts WHERE kind='event'")
    for r in evs:
        if r.get("deleted") == "1":
            continue
        c.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                  (int(r["_id"]), int(r.get("calendar_id") or 0), r.get("title"), ms_iso(r.get("dtstart")), ms_iso(r.get("dtend")),
                   int(r.get("allDay") or 0), r.get("eventLocation") or None, r.get("rrule"), r.get("description") or None))
        _fts(c, "event", int(r["_id"]), r.get("title"), " ".join(filter(None, [r.get("eventLocation"), r.get("description")])))
    now = int(dt.datetime.now().timestamp() * 1000)
    a, b = now - 90 * 86400000, now + 365 * 86400000
    c.execute("DELETE FROM event_instances")
    try:
        for r in query(f"content://com.android.calendar/instances/when/{a}/{b}", ["event_id", "begin", "end"]):
            c.execute("INSERT OR IGNORE INTO event_instances VALUES (?,?,?)", (int(r["event_id"]), ms_iso(r["begin"]), ms_iso(r["end"])))
    except RuntimeError:
        pass  # instances are a convenience; events are already stored
    return {"events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0]}


def _fts(c, kind, ref, title, body):
    c.execute("DELETE FROM phone_fts WHERE kind=? AND ref=?", (kind, str(ref)))
    if title or body:
        c.execute("INSERT INTO phone_fts(kind, ref, title, body) VALUES (?,?,?,?)", (kind, str(ref), title or "", body or ""))


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def build_identities(c):
    """One row per person, keyed by phone (or 'lid:<digits>' when no phone is known). Sources: agent.db `people`
    (WhatsApp contact name, push name, LID, owner nickname), bridge whatsapp.db (LID map / names, fallback), phone
    contacts, call-log cached names. Rebuilt every sync; ids stay stable through the UNIQUE key."""
    ppl = {}

    def row(phone=None, lid=None):
        key = phone or (f"lid:{lid}" if lid else None)
        if not key:
            return {}
        return ppl.setdefault(key, {"phone": phone, "lid": lid})

    try:
        with _ro(AGENT_DB) as a:
            for phone, lid, cn, pn, nick in a.execute("SELECT phone, lid, contact_name, push_name, nickname FROM people"):
                r = row(phone or None, lid or None)
                r["lid"] = r.get("lid") or lid
                r["wa_name"], r["push_name"], r["nickname"] = cn, pn, nick
    except sqlite3.Error:
        pass
    try:
        with _ro(WA_DB) as w:
            for lid, pn in w.execute("SELECT lid, pn FROM whatsmeow_lid_map"):
                l, p = str(lid or "").split("@")[0], str(pn or "").split("@")[0]
                if l and p:
                    r = row(p)
                    r["lid"] = r.get("lid") or l
                    lr = ppl.pop(f"lid:{l}", None)
                    if lr:
                        for k, v in lr.items():
                            r[k] = r.get(k) or v
            for jid, full, push in w.execute("SELECT their_jid, full_name, push_name FROM whatsmeow_contacts"):
                j = str(jid or "")
                d = j.split("@")[0]
                if d and j.endswith("@s.whatsapp.net"):
                    r = row(d)
                    r["wa_name"] = r.get("wa_name") or full
                    r["push_name"] = r.get("push_name") or push
    except sqlite3.Error:
        pass
    for cid, phone, name, nicks in c.execute("""SELECT p.contact_id, p.phone, k.display_name, k.nicknames
                                                FROM contact_phones p JOIN contacts k USING (contact_id)"""):
        r = row(phone)
        r["contact_id"], r["contact_name"] = cid, name
        if nicks:
            r["phone_nicks"] = nicks
    for phone, name, _ in c.execute("SELECT phone, name, MAX(ms) FROM calls WHERE phone<>'' AND name IS NOT NULL GROUP BY phone"):
        r = row(phone)
        r["call_name"] = r.get("call_name") or name
    now = dt.datetime.now().isoformat(timespec="seconds")
    seen = set()
    for key, r in ppl.items():
        nick = "; ".join(filter(None, [r.get("nickname"), r.get("phone_nicks")])) or None
        name = r.get("nickname") or r.get("contact_name") or r.get("wa_name") or r.get("push_name") or r.get("call_name")
        c.execute("""INSERT INTO identities (key, phone, lid, contact_id, contact_name, wa_name, push_name, nickname, call_name,
                     name, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET phone=excluded.phone,
                     lid=excluded.lid, contact_id=excluded.contact_id, contact_name=excluded.contact_name,
                     wa_name=excluded.wa_name, push_name=excluded.push_name, nickname=excluded.nickname,
                     call_name=excluded.call_name, name=excluded.name, updated_at=excluded.updated_at""",
                  (key, r.get("phone"), r.get("lid"), r.get("contact_id"), r.get("contact_name"), r.get("wa_name"),
                   r.get("push_name"), nick, r.get("call_name"), name, now))
        seen.add(key)
    c.execute("DELETE FROM identities WHERE updated_at<>?", (now,))
    c.execute("DELETE FROM phone_fts WHERE kind='person'")
    c.execute("""INSERT INTO phone_fts(kind, ref, title, body) SELECT 'person', id, COALESCE(name,''),
                 TRIM(COALESCE(contact_name,'')||' '||COALESCE(wa_name,'')||' '||COALESCE(push_name,'')||' '||
                 COALESCE(nickname,'')||' '||COALESCE(call_name,'')) FROM identities WHERE name IS NOT NULL""")
    return len(seen)


def sync():
    if not phone_sync.connect():
        return {"error": "phone not reachable (Tailscale / wireless adb off)", "device": phone_sync.DEVICE}
    c = db()
    out = {}
    for name, fn in (("contacts", sync_contacts), ("calls", sync_calls), ("calendar", sync_calendar)):
        try:
            out[name] = fn(c)
            c.commit()
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            out[name] = {"error": str(e)[:200]}
    out["identities"] = build_identities(c)
    meta(c, "last_sync", dt.datetime.now().isoformat(timespec="seconds"))
    c.commit()
    return out


# ---------- call recordings ----------

def _rec_time(name, mtime):
    """Start time of a recording: from the file name (yyyyMMdd_HHmmss / yyMMdd_HHmmss / yyyy-MM-dd HH-mm-ss) else mtime."""
    for rx, fmt in ((r"(20\d{6})[_\- ]?(\d{6})", "%Y%m%d%H%M%S"), (r"(\d{6})[_\-](\d{6})", "%y%m%d%H%M%S"),
                    (r"(20\d\d-\d\d-\d\d)[ _](\d\d-\d\d-\d\d)", "%Y-%m-%d%H-%M-%S")):
        m = re.search(rx, name)
        if m:
            try:
                return int(dt.datetime.strptime(m[1] + m[2], fmt).timestamp() * 1000), True
            except ValueError:
                pass
    return int(mtime) * 1000, False


def _link(c, name, mtime):
    """The call this recording belongs to: same number in the file name if present, closest start time (±10 min)."""
    ms, exact = _rec_time(name, mtime)
    digits = norm(next(iter(re.findall(r"\+?\d{9,13}", name)), ""))
    q = "SELECT id, phone, ms, duration FROM calls WHERE ms BETWEEN ? AND ?" + (" AND phone=?" if digits else "")
    args = [ms - 600000 - (0 if exact else 7200000), ms + 600000] + ([digits] if digits else [])
    best = None
    for cid, phone, cms, dur in c.execute(q, args):
        # mtime is the END of the call: compare it with start + duration
        d = abs(cms - ms) if exact else abs(cms + dur * 1000 - ms)
        if best is None or d < best[0]:
            best = (d, cid, phone)
    return (best[1], best[2]) if best else (None, digits or None)


def recordings(transcribe_n=3):
    """Copy new call recordings (adb pull, read-only), link each to a call-log row, transcribe up to N pending ones."""
    c = db()
    found, new = 0, 0
    if phone_sync.connect():
        os.makedirs(REC_DIR, exist_ok=True)
        for folder in REC_DIRS:
            for path, (size, mtime) in phone_sync.listing(folder).items():
                if not AUDIO.search(path):
                    continue
                found += 1
                old = c.execute("SELECT size FROM recordings WHERE phone_path=?", (path,)).fetchone()
                if old and old[0] == size:
                    continue
                local = os.path.join(REC_DIR, os.path.basename(path))
                if os.path.exists(local) and os.path.getsize(local) != size and not old:
                    base, ext = os.path.splitext(local)
                    local = f"{base}_{mtime}{ext}"
                rc, out = phone_sync.adb("pull", "-a", path, local, timeout=600)
                if rc != 0:
                    continue
                call_id, phone = _link(c, os.path.basename(path), mtime)
                c.execute("""INSERT INTO recordings (phone_path, local_path, size, mtime, call_id, phone, status)
                             VALUES (?,?,?,?,?,?,'new') ON CONFLICT(phone_path) DO UPDATE SET local_path=excluded.local_path,
                             size=excluded.size, mtime=excluded.mtime, status='new'""", (path, local, size, mtime, call_id, phone))
                new += 1
        c.commit()
        meta(c, "recordings_scan", dt.datetime.now().isoformat(timespec="seconds"))
        c.commit()
    done = []
    for rid, local in c.execute("SELECT id, local_path FROM recordings WHERE status='new' ORDER BY id LIMIT ?",
                                (int(transcribe_n),)).fetchall():
        text, err = _transcribe(local)
        c.execute("UPDATE recordings SET transcript=?, status=? WHERE id=?", (text, "done" if text is not None else "failed", rid))
        if text:
            who = c.execute("""SELECT COALESCE(i.name, '') FROM recordings r LEFT JOIN identities i ON i.phone=r.phone
                               WHERE r.id=?""", (rid,)).fetchone()[0]
            _fts(c, "recording", rid, who, text)
        c.commit()
        done.append({"id": rid, "ok": text is not None, **({"error": err} if err else {})})
    total = c.execute("SELECT COUNT(*), SUM(status='new'), SUM(status='done') FROM recordings").fetchone()
    return {"dirs_scanned": len(REC_DIRS), "audio_files_on_phone": found, "copied": new, "transcribed": done,
            "total": total[0], "pending": total[1] or 0, "with_transcript": total[2] or 0, "local_dir": REC_DIR}


def _transcribe(path):
    """Transcribe with the agent's own faster-whisper setup (agent.transcribe, same model as voice notes), run in the
    agent's venv. Returns (text, error)."""
    py = os.path.join(ROOT, "agent", ".venv", "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
    if not os.path.exists(py):
        return None, "agent venv not found"
    code = ("import sys, os, json; d = sys.argv[1]; os.chdir(d); sys.path.insert(0, d); import agent; "
            "print(json.dumps({'text': agent.transcribe(sys.argv[2])}))")
    try:
        r = subprocess.run([py, "-X", "utf8", "-c", code, os.path.join(ROOT, "agent"), path], capture_output=True,
                           text=True, encoding="utf-8", timeout=3600, creationflags=phone_sync.NO_WINDOW)
        line = [ln for ln in r.stdout.splitlines() if ln.startswith('{"text"')]
        if line:
            return json.loads(line[-1])["text"], None
        return None, (r.stderr or r.stdout)[-300:]
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, str(e)[:200]


# ---------- read commands ----------

def _name(r):
    return r or UNKNOWN


def _ident(c, q):
    """Identities matching a name / phone / LID / '#id'."""
    cols = "id, key, phone, lid, contact_id, contact_name, wa_name, push_name, nickname, call_name, name"
    q = q.strip()
    if q.startswith("#") and q[1:].isdigit():
        rows = c.execute(f"SELECT {cols} FROM identities WHERE id=?", (int(q[1:]),)).fetchall()
    elif re.fullmatch(r"[\d+\-\s()]{6,}", q):
        d = norm(q)
        raw = re.sub(r"\D", "", q)
        rows = c.execute(f"SELECT {cols} FROM identities WHERE phone=? OR lid=? OR phone=? OR lid=?", (d, d, raw, raw)).fetchall()
    else:
        like = f"%{q}%"
        rows = c.execute(f"""SELECT {cols} FROM identities WHERE name LIKE ? OR contact_name LIKE ? OR wa_name LIKE ?
                             OR push_name LIKE ? OR nickname LIKE ? OR call_name LIKE ?
                             ORDER BY (name = ?) DESC, (SELECT COUNT(*) FROM calls k WHERE k.phone = identities.phone AND k.phone <> '') DESC,
                             (contact_id IS NOT NULL) DESC, (phone IS NOT NULL) DESC LIMIT 10""", (like,) * 6 + (q,)).fetchall()
    keys = cols.replace(" ", "").split(",")
    return [dict(zip(keys, r)) for r in rows]


def _wa_chats(phone, lid):
    jids = [j for j in ((phone and f"{phone}@s.whatsapp.net"), (lid and f"{lid}@lid")) if j]
    if not jids:
        return []
    try:
        with _ro(MSG_DB) as m:
            ph = ",".join("?" * len(jids))
            rows = m.execute(f"SELECT chat_jid, COUNT(*), MAX(timestamp) FROM messages WHERE chat_jid IN ({ph}) GROUP BY chat_jid",
                             jids).fetchall()
            ids = [x for x in (phone, lid) if x]
            grp = m.execute(f"""SELECT COUNT(*), COUNT(DISTINCT chat_jid), MAX(timestamp) FROM messages
                                WHERE chat_jid LIKE '%@g.us' AND sender IN ({','.join('?' * len(ids))})""", ids).fetchone()
    except sqlite3.Error:
        return []
    out = [{"chat_jid": j, "messages": n, "last": (t or "")[:19]} for j, n, t in rows]
    if grp and grp[0]:
        out.append({"in_groups": True, "messages": grp[0], "groups": grp[1], "last": (grp[2] or "")[:19]})
    return out


def who(q):
    c = db()
    res = []
    for p in _ident(c, q)[:5]:
        calls = [dict(zip(("ts", "type", "duration_s"), r)) for r in c.execute(
            "SELECT ts, type, duration FROM calls WHERE phone=? ORDER BY ms DESC LIMIT 5", (p["phone"] or "-",))]
        n_calls = c.execute("SELECT COUNT(*) FROM calls WHERE phone=?", (p["phone"] or "-",)).fetchone()[0]
        contact = None
        if p["contact_id"]:
            k = c.execute("SELECT nicknames, emails, org FROM contacts WHERE contact_id=?", (p["contact_id"],)).fetchone()
            if k:
                contact = {k2: v for k2, v in zip(("nicknames", "emails", "org"), k) if v}
        recs = c.execute("SELECT COUNT(*) FROM recordings WHERE phone=?", (p["phone"] or "-",)).fetchone()[0]
        res.append({"id": f"#{p['id']}", "name": _name(p["name"]),
                    "names": {k: p[k] for k in ("contact_name", "wa_name", "push_name", "nickname", "call_name") if p[k]},
                    **({"contact": contact} if contact else {}), "calls_total": n_calls, "last_calls": calls,
                    "recordings": recs, "whatsapp": _wa_chats(p["phone"], p["lid"]),
                    "ids_for_tools": {"phone": p["phone"], "lid": p["lid"]}})
    return {"query": q, "matches": len(res), "people": res,
            "_note": "To the owner: names only. ids_for_tools are for other tools (jid = phone@s.whatsapp.net / lid@lid)."}


def calls(n=30, who_q=None):
    c = db()
    where, args = "", []
    if who_q:
        phones = [p["phone"] for p in _ident(c, who_q) if p["phone"]]
        if not phones:
            return {"calls": [], "note": "no such person"}
        where, args = f"WHERE c.phone IN ({','.join('?' * len(phones))})", phones
    rows = c.execute(f"""SELECT c.id, c.ts, c.type, c.duration, COALESCE(i.name, c.name), i.id,
                         (SELECT COUNT(*) FROM recordings r WHERE r.call_id = c.id)
                         FROM calls c LEFT JOIN identities i ON i.phone = c.phone AND c.phone <> ''
                         {where} ORDER BY c.ms DESC LIMIT ?""", args + [int(n)]).fetchall()
    return {"calls": [{"call_id": r[0], "ts": r[1], "type": r[2], "duration_s": r[3], "name": _name(r[4]),
                       **({"person": f"#{r[5]}"} if r[5] else {}), **({"recording": True} if r[6] else {})} for r in rows]}


def events(frm=None, to=None):
    c = db()
    frm = frm or dt.date.today().isoformat()
    to = to or (dt.date.fromisoformat(frm[:10]) + dt.timedelta(days=30)).isoformat()
    rows = c.execute("""SELECT e.id, e.title, i.begin, i.end, e.all_day, e.location, k.name
                        FROM event_instances i JOIN events e ON e.id = i.event_id LEFT JOIN calendars k ON k.id = e.calendar_id
                        WHERE i.begin >= ? AND i.begin < ? ORDER BY i.begin""", (frm, to + "T99")).fetchall()
    if not rows:  # instances not available: plain events
        rows = c.execute("""SELECT e.id, e.title, e.start, e.end, e.all_day, e.location, k.name FROM events e
                            LEFT JOIN calendars k ON k.id = e.calendar_id WHERE e.start >= ? AND e.start < ? ORDER BY e.start""",
                         (frm, to + "T99")).fetchall()
    return {"from": frm, "to": to, "events": [
        {"id": r[0], "title": r[1], "start": r[2][:10] if r[4] else r[2], "end": r[3][:10] if (r[4] and r[3]) else r[3],
         **({"all_day": True} if r[4] else {}), **({"location": r[5]} if r[5] else {}), "calendar": r[6]} for r in rows]}


def index(limit=50):
    """Table of contents: one row per person with calls or direct WhatsApp messages; sorted by last contact."""
    c = db()
    agg = {}
    for phone, n, last, mins in c.execute("SELECT phone, COUNT(*), MAX(ts), SUM(duration) FROM calls WHERE phone<>'' GROUP BY phone"):
        agg[("phone", phone)] = {"calls": n, "last_call": last, "talk_min": round((mins or 0) / 60)}
    try:
        with _ro(MSG_DB) as m:
            for jid, n, last in m.execute("""SELECT chat_jid, COUNT(*), MAX(timestamp) FROM messages
                                             WHERE chat_jid LIKE '%@s.whatsapp.net' OR chat_jid LIKE '%@lid' GROUP BY chat_jid"""):
                d = jid.split("@")[0]
                key = ("phone", d) if jid.endswith("@s.whatsapp.net") else ("lid", d)
                agg.setdefault(key, {}).update({"messages": n, "last_message": (last or "")[:19].replace(" ", "T")})
    except sqlite3.Error:
        pass
    people = {}
    for pid, phone, lid, name in c.execute("SELECT id, phone, lid, name FROM identities"):
        for key in (("phone", phone), ("lid", lid)):
            if key[1] and key in agg:
                p = people.setdefault(pid, {"id": f"#{pid}", "name": _name(name)})
                for k, v in agg.pop(key).items():
                    if k not in p:
                        p[k] = v
                    else:
                        p[k] = p[k] + v if k in ("calls", "messages", "talk_min") else max(p[k], v)
    for key, v in agg.items():  # numbers with no identity row
        people[f"x{key}"] = {"id": None, "name": UNKNOWN, **v}
    for p in people.values():
        p["last_contact"] = max(p.get("last_call") or "", p.get("last_message") or "")
    rows = sorted(people.values(), key=lambda p: p["last_contact"], reverse=True)
    return {"people_total": len(rows), "shown": min(len(rows), int(limit)), "people": rows[:int(limit)]}


def _fts_query(q):
    toks = [t for t in re.split(r"\s+", q.strip()) if t]
    return " ".join('"' + t.replace('"', '""') + '"*' for t in toks)


def search(q, limit=20):
    c = db()
    fq = _fts_query(q)
    if not fq:
        return {"results": []}
    out = []
    for kind, ref, title, snip in c.execute("""SELECT kind, ref, title, snippet(phone_fts, 3, '[', ']', '…', 12)
                                               FROM phone_fts WHERE phone_fts MATCH ? ORDER BY rank LIMIT ?""", (fq, int(limit))):
        r = {"kind": kind, "title": title or UNKNOWN}
        if kind == "person":
            r["who"] = f"#{ref}"
        elif kind == "event":
            e = c.execute("SELECT start, location FROM events WHERE id=?", (int(ref),)).fetchone()
            r.update({"event_id": int(ref), "start": e and e[0]})
        elif kind == "recording":
            rr = c.execute("SELECT call_id, local_path FROM recordings WHERE id=?", (int(ref),)).fetchone()
            r.update({"recording_id": int(ref), "call_id": rr and rr[0], "file": rr and rr[1], "snippet": snip})
        elif kind == "contact":
            r.update({"contact_id": int(ref), "snippet": snip})
        out.append(r)
    return {"results": out}


def status():
    c = db()
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    return {"device": phone_sync.DEVICE, "reachable": phone_sync.connect(), "db": DB, "last_sync": meta(c, "last_sync"),
            "contacts": q("SELECT COUNT(*) FROM contacts"), "contact_phones": q("SELECT COUNT(*) FROM contact_phones"),
            "calls": q("SELECT COUNT(*) FROM calls"), "first_call": q("SELECT MIN(ts) FROM calls"),
            "last_call": q("SELECT MAX(ts) FROM calls"), "events": q("SELECT COUNT(*) FROM events"),
            "event_instances": q("SELECT COUNT(*) FROM event_instances"), "identities": q("SELECT COUNT(*) FROM identities"),
            "named_identities": q("SELECT COUNT(*) FROM identities WHERE name IS NOT NULL"),
            "recordings": q("SELECT COUNT(*) FROM recordings"), "recordings_scan": meta(c, "recordings_scan")}


def _opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


def _words(args, flags):
    skip = {i + 1 for i, a in enumerate(args) if a in flags} | {i for i, a in enumerate(args) if a in flags}
    return [a for i, a in enumerate(args) if i not in skip]


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else "status"), sys.argv[2:]
    if cmd == "sync":
        out = sync()
    elif cmd == "who" and args:
        out = who(" ".join(args))
    elif cmd == "calls":
        rest = _words(args, {"--who"})
        out = calls(int(rest[0]) if rest and rest[0].isdigit() else 30, _opt(args, "--who"))
    elif cmd == "events":
        out = events(_opt(args, "--from"), _opt(args, "--to"))
    elif cmd == "index":
        out = index(int(args[0]) if args and args[0].isdigit() else 50)
    elif cmd == "search" and args:
        out = search(" ".join(_words(args, {"--limit"})), int(_opt(args, "--limit", 20)))
    elif cmd == "recordings":
        out = recordings(int(_opt(args, "--transcribe", 3)))
    elif cmd == "status":
        out = status()
    else:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(out, ensure_ascii=False, indent=1))
