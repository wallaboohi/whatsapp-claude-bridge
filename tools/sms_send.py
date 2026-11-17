"""Send an SMS from the owner's phone ONLY through a draft + the owner's approval (owner 17.11, third request).
Same path as WhatsApp drafts: draft -> a card with the FULL text in the control group -> the owner's reaction on the card:
✅ sends exactly this SMS once, ❌ cancels. One approval = one SMS to one recipient. No approval = nothing is sent.
A draft that waits more than 30 minutes expires (like WhatsApp drafts).

Usage (from the tool root; Hebrew text always from a UTF-8 file):
  uv run -q --no-project python -I -X utf8 tools/sms_send.py draft <number|name> <textfile> [--name <label>]   creates the draft + card
  uv run -q --no-project python -I -X utf8 tools/sms_send.py list                                              pending drafts
  uv run -q --no-project python -I -X utf8 tools/sms_send.py card <id>                                         post the card again
  uv run -q --no-project python -I -X utf8 tools/sms_send.py react <card_message_id> <emoji>                   (agent only) the owner's reaction
How it sends: the phone must be UNLOCKED (Android 16 has no safe adb command that sends while locked). The tool opens the compose
screen with the text filled in and taps Send, then checks the phone's sent box. Locked phone: the draft stays pending and the
group is told to unlock and react again.
"""
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import phone_sync  # noqa: E402

CFG = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
DB = os.path.join(ROOT, "agent", "sms.db")
STORE = os.path.join(ROOT, "whatsapp-bridge", "store")
GROUP = CFG["claude_group"]
EXPIRE_MIN = 30
OWNER_APPROVE = ("✅", "✔️", "👍")
QUOTE = "'" + chr(92) + "''"  # shell-safe single quote: '\''


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS sms_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, to_number TEXT, to_name TEXT, body TEXT,
                 status TEXT DEFAULT 'pending', created TEXT, card_id TEXT, note TEXT, decided TEXT)""")
    return c


def ro(name):
    return sqlite3.connect(f"file:{os.path.join(STORE, name)}?mode=ro", uri=True)


def bridge_send(text):
    token = open(os.path.join(STORE, "api_token")).read().strip()
    req = urllib.request.Request(CFG["bridge_url"] + "/send", data=json.dumps({"recipient": GROUP, "message": text}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode() or "{}")


def say(text):
    try:
        bridge_send(CFG.get("reply_prefix", "🤖 ") + text)
    except Exception as e:
        print("say failed", e, file=sys.stderr)


def find_card_id(text):
    m = ro("messages.db")
    for _ in range(10):
        r = m.execute("SELECT id FROM messages WHERE chat_jid = ? AND is_from_me = 1 AND content = ? ORDER BY timestamp DESC LIMIT 1",
                      (GROUP, text)).fetchone()
        if r:
            return r[0]
        time.sleep(1)
    return ""


def label_for(number):
    try:
        r = ro("whatsapp.db").execute("SELECT COALESCE(NULLIF(full_name,''), push_name) FROM whatsmeow_contacts WHERE their_jid = ?",
                                      (number.lstrip("+") + "@s.whatsapp.net",)).fetchone()
        return (r[0] if r else "") or ""
    except Exception:
        return ""


def resolve(to):
    """number or contact name -> (number in +972 form, label). A name must match exactly one saved contact."""
    digits = re.sub(r"[^\d+]", "", to)
    if re.fullmatch(r"\+?\d{9,13}", digits):
        n = digits
        if n.startswith("0"):
            n = "+972" + n[1:]
        elif not n.startswith("+"):
            n = "+" + n
        return n, ""
    rows = ro("whatsapp.db").execute(
        "SELECT their_jid, COALESCE(NULLIF(full_name,''), push_name) FROM whatsmeow_contacts WHERE full_name LIKE ? "
        "AND their_jid LIKE '%@s.whatsapp.net'", (f"%{to}%",)).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"שם '{to}' מתאים ל-{len(rows)} אנשי קשר; צריך נמען אחד מדויק (או מספר)")
    return "+" + rows[0][0].split("@")[0], rows[0][1]


def card_text(i, name, body):
    return (f"📱 *טיוטת SMS #{i} ל{name or 'איש קשר לא מזוהה'}* (ממתינה לאישור)\n\n{body}\n\n"
            f"הגב ✅ לשליחה ב-SMS (פעם אחת בלבד) · ❌ לביטול · פג תוקף אחרי {EXPIRE_MIN} דקות")


def post_card(c, i):
    name, body = c.execute("SELECT to_name, body FROM sms_drafts WHERE id = ?", (i,)).fetchone()
    text = card_text(i, name, body)
    bridge_send(text)
    cid = find_card_id(text)
    c.execute("UPDATE sms_drafts SET card_id = ? WHERE id = ?", (cid, i))
    c.commit()
    return cid


def draft(to, textfile, name=""):
    body = open(textfile, encoding="utf-8").read().strip()
    if not body or "�" in body or re.search(r"\?{4,}", body):
        raise SystemExit("הטקסט ריק או נראה פגום (קידוד); כתוב אותו לקובץ UTF-8")
    number, label = resolve(to)
    name = name or label or label_for(number)
    c = db()
    i = c.execute("INSERT INTO sms_drafts (to_number, to_name, body, created) VALUES (?,?,?,?)",
                  (number, name, body, dt.datetime.now().isoformat(timespec="seconds"))).lastrowid
    c.commit()
    cid = post_card(c, i)
    print(json.dumps({"draft": i, "card_id": cid, "to": name or "איש קשר לא מזוהה"}, ensure_ascii=False))


def locked():
    rc, out = phone_sync.adb("shell", "dumpsys window | grep -E 'isKeyguardShowing'", timeout=30)
    return "isKeyguardShowing=true" in out


def _tap_send():
    """Find the Send button in the compose screen (uiautomator) and tap it."""
    for _ in range(12):
        time.sleep(1.5)
        phone_sync.adb("shell", "uiautomator dump /sdcard/_ui.xml", timeout=30)
        rc, xml = phone_sync.adb("shell", "cat /sdcard/_ui.xml", timeout=30)
        try:
            root = ET.fromstring(xml[xml.index("<?xml"):] if "<?xml" in xml else xml)
        except (ET.ParseError, ValueError):
            continue
        for n in root.iter("node"):
            desc = (n.get("content-desc") or "") + " " + (n.get("text") or "")
            if re.search(r"Send SMS|Send message|שלח", desc, re.I) and n.get("clickable") == "true":
                m = re.findall(r"\d+", n.get("bounds") or "")
                if len(m) == 4:
                    x, y = (int(m[0]) + int(m[2])) // 2, (int(m[1]) + int(m[3])) // 2
                    phone_sync.adb("shell", f"input tap {x} {y}", timeout=20)
                    return True
    return False


def phone_send(number, body):
    """True when the phone's sent box holds the new SMS. Raises RuntimeError('locked') / ('unreachable')."""
    if not phone_sync.connect():
        raise RuntimeError("unreachable")
    if locked():
        raise RuntimeError("locked")
    since = int(time.time() * 1000) - 5000
    phone_sync.adb("shell", "input keyevent KEYCODE_WAKEUP", timeout=20)
    q = body.replace("'", QUOTE)
    phone_sync.adb("shell", f"am start -a android.intent.action.SENDTO -d 'sms:{number}' --es sms_body '{q}'", timeout=30)
    if not _tap_send():
        phone_sync.adb("shell", "input keyevent KEYCODE_BACK", timeout=20)
        return False
    for _ in range(10):
        time.sleep(2)
        rc, out = phone_sync.adb("shell", f"content query --uri content://sms/sent --projection address:date:body --where \"date>{since}\"",
                                 timeout=30)
        if "body=" in out:
            return True
    return False


def approve(i):
    c = db()
    row = c.execute("SELECT to_number, to_name, body, status, created FROM sms_drafts WHERE id = ?", (i,)).fetchone()
    if not row:
        return "אין טיוטת SMS כזו"
    number, name, body, status, created = row
    who = name or "איש קשר לא מזוהה"
    if status != "pending":
        return f"טיוטת ה-SMS ל{who} כבר טופלה ({status}); לא נשלח שוב"
    if dt.datetime.now() - dt.datetime.fromisoformat(created) > dt.timedelta(minutes=EXPIRE_MIN):
        c.execute("UPDATE sms_drafts SET status = 'expired' WHERE id = ?", (i,))
        c.commit()
        return f"טיוטת ה-SMS ל{who} פגה (עברו {EXPIRE_MIN} דקות). צריך טיוטה חדשה"
    if c.execute("UPDATE sms_drafts SET status = 'sending' WHERE id = ? AND status = 'pending'", (i,)).rowcount != 1:
        return "הטיוטה כבר בשליחה"  # one approval can never send twice
    c.commit()
    try:
        ok = phone_send(number, body)
        status, msg = ("sent", f"✅ ה-SMS ל{who} נשלח.") if ok else (
            "failed", f"⚠️ לא הצלחתי לוודא שה-SMS ל{who} נשלח (אולי יצא): בדוק בטלפון. לא אנסה שוב לבד.")
    except RuntimeError as e:
        if str(e) == "locked":
            c.execute("UPDATE sms_drafts SET status = 'pending', note = 'phone locked' WHERE id = ?", (i,))
            c.commit()
            return f"🔒 הטלפון נעול, ה-SMS ל{who} לא נשלח. פתח את המסך והגב ✅ שוב על הכרטיס (הטיוטה עדיין ממתינה)."
        status, msg = "failed", f"⚠️ הטלפון לא זמין ב-ADB, ה-SMS ל{who} לא נשלח."
    c.execute("UPDATE sms_drafts SET status = ?, decided = ? WHERE id = ?", (status, dt.datetime.now().isoformat(timespec="seconds"), i))
    c.commit()
    return msg


def react(card_id, emoji):
    c = db()
    r = c.execute("SELECT id, to_name, status FROM sms_drafts WHERE card_id = ?", (card_id,)).fetchone()
    if not r:
        return  # not an SMS card
    i, name, status = r
    if emoji == "❌":
        if status == "pending":
            c.execute("UPDATE sms_drafts SET status = 'cancelled', decided = ? WHERE id = ?",
                      (dt.datetime.now().isoformat(timespec="seconds"), i))
            c.commit()
            say(f"_טיוטת ה-SMS ל{name or 'איש קשר לא מזוהה'} בוטלה._")
        return
    if emoji in OWNER_APPROVE:
        say(f"_{approve(i)}_")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "draft" and len(argv) >= 4:
        draft(argv[2], argv[3], argv[argv.index("--name") + 1] if "--name" in argv else "")
    elif cmd == "list":
        for r in db().execute("SELECT id, to_name, status, created FROM sms_drafts WHERE status = 'pending' ORDER BY id"):
            print(r[0], r[1] or "איש קשר לא מזוהה", r[2], r[3])
    elif cmd == "card" and len(argv) >= 3:
        print(post_card(db(), int(argv[2])))
    elif cmd == "react" and len(argv) >= 4:
        react(argv[2], argv[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
