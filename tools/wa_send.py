import json, os, sys, urllib.error, urllib.request
# send a WhatsApp message through the local bridge:
#   wa_send.py <recipient> <utf8 text file> [media path] [--quote=<message id>]
# In draft mode the bridge does not send to other people; it prepares a draft for the owner instead.
args = [a for a in sys.argv[1:] if not a.startswith("--quote=")]
quote = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--quote=")), "")
to, text_file = args[0], args[1]
body = {"recipient": to, "message": open(text_file, encoding="utf-8").read().strip()}
if len(args) > 2:
    body["media_path"] = args[2]
if quote:
    body["quoted_id"] = quote  # reply quoting that message (same chat)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_owner_side(rcpt):
    """The owner's own numbers and control groups (send_policy.json) — everyone else gets the 🤖 mark."""
    try:
        pol = json.load(open(os.path.join(ROOT, "whatsapp-bridge", "store", "send_policy.json"), encoding="utf-8"))
    except Exception:
        return False
    user = rcpt.split("@")[0]
    mine = [pol.get("control_group") or ""] + (pol.get("control_groups") or []) + (pol.get("owner") or [])
    return any(user == m.split("@")[0] for m in mine if m)


# owner rule (09-10.11): everything that goes out to other people carries 🤖 — also drafts he approves with ✅,
# so the mark is added here, before the draft is created
if body["message"] and not body["message"].startswith("🤖") and not _is_owner_side(to):
    body["message"] = "🤖 " + body["message"]
token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token")).read().strip()  # bridge API password
req = urllib.request.Request("http://127.0.0.1:8765/api/send", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
def _log(result):
    # יומן פעולות: מי (Claude), מתי, למי, מה נשלח (תחילת הטקסט), והתוצאה — נכתב גם אם השליחה נכשלה
    import datetime
    try:
        line = "%s | Claude | to=%s | media=%s | text=%r | result=%s\n" % (
            datetime.datetime.now().isoformat(timespec="seconds"), to, body.get("media_path", "-"),
            body["message"][:80], str(result)[:200].replace("\n", " "))
        with open(os.path.join(ROOT, "agent", "actions.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

try:
    out = urllib.request.urlopen(req, timeout=180).read().decode()
    print(out)
    _log(out)
except urllib.error.HTTPError as e:
    out = e.read().decode()
    print(out)
    _log("HTTP %s %s" % (e.code, out))
