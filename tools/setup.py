"""Setup wizard for the YB AI Bridge for WhatsApp (Windows). Run `setup.cmd` from the tool root.

Steps: bridge binary → WhatsApp pairing (QR) → control group → config files → dependencies → MCP registration →
logon tasks → dashboard app → self-test. Safe to re-run: finished steps are skipped.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "whatsapp-bridge")
STORE = os.path.join(BRIDGE, "store")
AGENT = os.path.join(ROOT, "agent")
REPO = "WallaBoohi/whatsapp-claude-bridge"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
lang = "he"


def say(he, en):
    print(he if lang == "he" else en, flush=True)


def ask(he, en, default=""):
    v = input((he if lang == "he" else en) + (f" [{default}]" if default else "") + ": ").strip()
    return v or default


def uv():
    return shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")


def step(n, he, en):
    say(f"\n━━ שלב {n}: {he}", f"\n━━ Step {n}: {en}")


def api(path, body=None):
    # the owner token (created by bridges >= 1.0.8) is needed to set the send policy; older bridges only have api_token
    p = os.path.join(STORE, "owner_token")
    tok = open(p if os.path.exists(p) else os.path.join(STORE, "api_token")).read().strip()
    req = urllib.request.Request("http://127.0.0.1:8765/api" + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def bridge_running():
    try:
        return api("/status").get("logged_in") is not None
    except Exception:
        return False


def main():
    global lang
    lang = "en" if input("Language / שפה — (h)ebrew / (e)nglish [h]: ").strip().lower().startswith("e") else "he"
    say("התקנת YB גשר AI לווצאפ", "Installing the YB AI Bridge for WhatsApp")

    step(1, "גשר ווצאפ", "WhatsApp bridge")
    exe = os.path.join(BRIDGE, "whatsapp-bridge.exe")
    if not os.path.exists(exe):
        go, gcc = shutil.which("go"), shutil.which("gcc")
        if go and gcc:
            say("בונה את הגשר…", "Building the bridge…")
            subprocess.run([go, "build", "-o", "whatsapp-bridge.exe", "."], cwd=BRIDGE, check=True, env={**os.environ, "CGO_ENABLED": "1"})
        else:
            say("מוריד את הגשר מה-Release האחרון…", "Downloading the bridge from the latest release…")
            urllib.request.urlretrieve(f"https://github.com/{REPO}/releases/latest/download/whatsapp-bridge.exe", exe)
    say("✔ הגשר קיים", "✔ bridge ready")

    step(2, "חיבור לחשבון ווצאפ", "Link your WhatsApp account")
    if not bridge_running():
        subprocess.Popen(["cmd.exe", "/c", "start", "WhatsApp bridge", os.path.join(ROOT, "run-bridge.cmd")])
        say("נפתח חלון עם קוד QR (וגם קובץ qr.png). בטלפון: ווצאפ ← מכשירים מקושרים ← קישור מכשיר, וסרוק.",
            "A window with a QR code opens (also qr.png). On the phone: WhatsApp → Linked devices → Link a device, and scan.")
        for _ in range(180):
            time.sleep(2)
            try:
                if api("/status").get("logged_in"):
                    break
            except Exception:
                pass
        else:
            say("✖ לא זוהה חיבור תוך 6 דקות. הרץ שוב את ההתקנה.", "✖ No link within 6 minutes. Run setup again.")
            sys.exit(1)
    say("✔ מחובר", "✔ linked")
    dev = sqlite3.connect(f"file:{os.path.join(STORE, 'whatsapp.db')}?mode=ro", uri=True).execute("SELECT jid, lid FROM whatsmeow_device").fetchone()
    me_pn = dev[0].split(":")[0].split("@")[0] + "@s.whatsapp.net"
    me_lid = (dev[1] or "").split(":")[0].split("@")[0] + "@lid" if dev[1] else ""

    step(3, "קבוצת הבקרה (השיחה שלך עם Claude)", "Control group (your chat with Claude)")
    cfg_path = os.path.join(AGENT, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else json.load(open(os.path.join(AGENT, "config.example.json"), encoding="utf-8"))
    created = False
    if not str(cfg.get("claude_group", "")).endswith("@g.us"):
        group = None
        if ask("ליצור עכשיו קבוצת Claude שבה רק אתה? כ/ל", "Create a Claude group with only you in it now? y/n", "כ" if lang == "he" else "y").lower() in ("כ", "y", "yes", "כן"):
            try:
                group = api("/create_group", {"name": "Claude", "participants": [],
                                              "description": "השיחה הפרטית שלך עם Claude" if lang == "he" else "Your private chat with Claude"}).get("jid")
                created = bool(group)
            except Exception as e:
                say(f"⚠ יצירת הקבוצה נכשלה ({e}) — ניצור אותה ידנית.", f"⚠ Could not create the group ({e}) — let's do it by hand.")
        if not group:
            say("צור בווצאפ קבוצה בשם Claude שבה אתה לבד (הוסף מישהו והוצא אותו), וכתוב בה:  claude-setup",
                "In WhatsApp create a group named Claude with only you in it (add someone and remove them), and write in it:  claude-setup")
        for _ in range(0 if group else 300):
            time.sleep(2)
            r = sqlite3.connect(f"file:{os.path.join(STORE, 'messages.db')}?mode=ro", uri=True).execute(
                "SELECT chat_jid FROM messages WHERE is_from_me = 1 AND trim(lower(content)) = 'claude-setup' AND chat_jid LIKE '%@g.us' "
                "ORDER BY rowid DESC LIMIT 1").fetchone()
            if r:
                group = r[0]
                break
        if not group:
            say("✖ ההודעה לא זוהתה תוך 10 דקות.", "✖ The message was not seen within 10 minutes.")
            sys.exit(1)
        cfg["claude_group"] = group
    say(f"✔ קבוצת בקרה: {cfg['claude_group']}", f"✔ control group: {cfg['claude_group']}")

    step(4, "הגדרות", "Settings")
    cfg["language"] = lang
    cfg["owner_name"] = ask("השם שלך (כך Claude יפנה אליך)", "Your name (how Claude refers to you)", cfg.get("owner_name", "").strip("<>") if not str(cfg.get("owner_name", "")).startswith("<") else "")
    voice = ask("לתמלל הודעות קוליות במחשב? (מוריד מודל של 1.6GB) כ/ל", "Transcribe voice notes locally? (downloads a 1.6 GB model) y/n", "כ" if lang == "he" else "y")
    cfg["transcribe"] = voice.lower() in ("כ", "y", "yes", "כן")
    if lang == "en":
        cfg["whisper_model"], cfg["whisper_language"] = "large-v3-turbo", "en"
    cfg["store_dir"] = "whatsapp-bridge/store"
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    api("/policy", {"owner": [me_pn] + ([me_lid] if me_lid else []), "control_group": cfg["claude_group"], "mode": "draft"})
    if created:  # the control group is the owner's own chat, so this goes out even in drafts-only mode
        api("/send", {"recipient": cfg["claude_group"], "message": "👋 זו השיחה שלך עם Claude. כתוב כאן כל בקשה." if lang == "he"
                      else "👋 This is your chat with Claude. Write any request here."})
    home = os.path.join(AGENT, "home")
    if not os.path.exists(os.path.join(home, "CLAUDE.md")):
        shutil.copytree(os.path.join(AGENT, "home.example"), home, dirs_exist_ok=True)
    say("✔ נשמר (מצב שליחה: טיוטות בלבד)", "✔ saved (send mode: drafts only)")

    step(5, "התקנת רכיבים", "Installing components")
    extras = ["--extra", "voice"] if cfg["transcribe"] else []
    subprocess.run([uv(), "sync", "-q"] + extras + ["--extra", "plugins"], cwd=AGENT, check=True)
    subprocess.run([uv(), "sync", "-q"], cwd=os.path.join(ROOT, "whatsapp-mcp-server"), check=True)
    run_agent = os.path.join(ROOT, "run-agent.cmd")
    say("✔ הרכיבים הותקנו", "✔ components installed")

    step(6, "חיבור ל-Claude Code", "Connect to Claude Code")
    claude = shutil.which("claude")
    if claude:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", "whatsapp"], capture_output=True)
        subprocess.run([claude, "mcp", "add", "--scope", "user", "whatsapp", "--", uv(), "--directory",
                        os.path.join(ROOT, "whatsapp-mcp-server").replace("\\", "/"), "run", "main.py"], check=False)
        say("✔ כלי ה-MCP רשום (whatsapp)", "✔ MCP server registered (whatsapp)")
    else:
        say("⚠ Claude Code לא נמצא — השיחה מווצאפ לא תעבוד עד שתתקין אותו.", "⚠ Claude Code not found — chatting from WhatsApp needs it.")

    step(7, "הפעלה אוטומטית", "Start at logon")
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "install-tasks.ps1")], check=False)
    subprocess.Popen(["conhost.exe", "--headless", "cmd.exe", "/c", run_agent], creationflags=NO_WINDOW)
    say("✔ הגשר והסוכן יעלו בכל כניסה ל-Windows", "✔ bridge and agent start at every Windows logon")

    step(8, "לוח הבקרה", "Dashboard app")
    rel = os.path.join(ROOT, "releases")
    os.makedirs(rel, exist_ok=True)
    if not os.path.exists(os.path.join(rel, "latest.json")):
        try:
            info = json.loads(urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest/download/latest.json", timeout=15).read().decode())
            urllib.request.urlretrieve(f"https://github.com/{REPO}/releases/latest/download/{info['file']}", os.path.join(rel, info["file"]))
            json.dump(info, open(os.path.join(rel, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            subprocess.run([uv(), "run", "--no-project", "python", os.path.join(ROOT, "tools", "release_dash.py"), "1.0.0"], cwd=ROOT, check=False)
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "tools", "install_dash.ps1")], check=False)

    step(9, "בדיקה עצמית", "Self-test")
    time.sleep(20)
    subprocess.run([uv(), "run", "-q", "--no-sync", "python", "-X", "utf8", os.path.join(ROOT, "tools", "selftest.py"), "--quiet"], cwd=AGENT)
    say("\n🎉 סיימנו. כתוב משהו בקבוצת Claude ותקבל תשובה.", "\n🎉 Done. Write something in the Claude group and you'll get an answer.")


if __name__ == "__main__":
    main()
