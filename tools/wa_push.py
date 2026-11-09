"""Queue a phone notification for the owner (a WhatsApp event that rings his phone a few seconds after your answer is sent).
Use it when your answer waits for his decision (a question before acting):
    wa_push.py "<short clean text>"      e.g.  wa_push.py "ויוי שואל: לשלוח את הדוח לצגאי?"
The text may also be a path to a UTF-8 file (Hebrew is safer that way). Short (up to 90 chars), no formatting.
The agent posts it in the conversation you are answering now, after your answer. Not for usage limits."""
import datetime, os, re, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
arg = " ".join(sys.argv[1:]).strip()
if os.path.isfile(arg):
    arg = open(arg, encoding="utf-8").read()
text = re.sub(r"[*_~`]", "", " ".join(arg.split()))[:90].strip()
if not text or "???" in text:
    sys.exit("empty or broken text — write it to a UTF-8 file and pass the path")
db = sqlite3.connect(os.environ.get("WA_AGENT_DB") or os.path.join(ROOT, "agent", "agent.db"), timeout=30)
db.execute("CREATE TABLE IF NOT EXISTS push_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, chat TEXT, kind TEXT, text TEXT, "
           "status TEXT DEFAULT 'pending', sent TEXT, result TEXT)")
now = datetime.datetime.now().isoformat(timespec="seconds")
db.execute("INSERT INTO push_queue (created, chat, kind, text) VALUES (?, NULL, 'question', ?)", (now, text))
db.commit()
with open(os.path.join(ROOT, "agent", "actions.log"), "a", encoding="utf-8") as f:
    f.write(f"{now} | Claude | push queued | {text}\n")
print("queued:", text)
