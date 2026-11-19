"""Talk to the AI *desktop apps* (Claude, ChatGPT, Copilot, Grok, ...) on this PC through Windows UI Automation.

Owner 18.11.2026: every PC has the same AI desktop apps (fleet/ai_apps_sync.ps1); the bridge must be able to use them as
providers: open the app, type the question, *listen* until the answer stops growing, and return the answer text.

Must run inside the logged-in user's desktop session (UI Automation cannot see windows from an ssh / service session).
From ssh use `--via-task`: the command re-runs itself as a one-off interactive scheduled task and waits for its output.

    uv run --no-project --with uiautomation python -X utf8 tools/desktop_ai.py list
    uv run --no-project --with uiautomation python -X utf8 tools/desktop_ai.py ask claude <utf8-question-file> [--timeout 180]
    ... --via-task            (from ssh: run in the user's session)
    ... ask <app> -           (the question on stdin, UTF-8: the bridge's "desktop-app" provider sends it this way over ssh)
    ... has <app>             (no typing: is the window open / is the app installed on this PC; the bridge's health check)

Output: one JSON line {"ok": bool, "app": ..., "answer": ..., "error": ...}. Never types unless the target window is in the
foreground (a check right before the paste), so a question never lands in another window.
"""
import json
import os
import re
import subprocess
import sys
import time

# owner 18.11: this runs under pythonw (no console) inside a scheduled task; a console child started without CREATE_NO_WINDOW
# makes Windows Terminal open a black window on the owner's screen for every call (one per powershell below)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

APPS = {  # key -> (window title regex, Start-menu name regex used to launch it; AppIDs differ per PC so they are looked up)
    "claude": (r"^Claude$", r"^Claude$"),
    "chatgpt": (r"^ChatGPT$", r"^ChatGPT$"),
    "copilot": (r"^Copilot$", r"^Copilot$"),  # exact: "GitHub Copilot" is another app (lap 18.11: it was picked as Copilot)
    "grok": (r"Grok", r"^Grok"),
    "github_copilot": (r"GitHub Copilot", r"^GitHub Copilot$"),
    "codex": (r"^Codex", r"^Codex"),
    "jan": (r"^Jan$", r"^Jan$"),
    "lmstudio": (r"LM Studio", r"^LM Studio$"),
}


def app_id(key):
    """The Start-menu AppID of the app on this PC (not the Chrome web-app shortcut of the same name)."""
    ps = "Get-StartApps | % { $_.Name + '|' + $_.AppID }"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    rx = re.compile(APPS[key][1], re.I)
    for ln in (r.stdout or "").splitlines():
        name, _, aid = ln.partition("|")
        if rx.search(name.strip()) and "Chrome._crx" not in aid:
            return aid.strip()
    return None


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def _ua():
    import uiautomation as ua  # noqa: WPS433
    return ua


def _clean(t):
    """Window titles carry bidi marks on a Hebrew Windows (e.g. U+202A Claude U+202C)."""
    return re.sub(r"[‎‏‪-‮⁦-⁩]", "", t or "").strip()


def find_window(key):
    ua = _ua()
    rx = re.compile(APPS[key][0], re.I)
    for w in ua.GetRootControl().GetChildren():
        try:
            if w.ControlTypeName == "WindowControl" and rx.search(_clean(w.Name)):
                return w
        except Exception:
            continue
    return None


def window_text(w, limit=4000):
    """All visible text of the window, in tree order (the conversation)."""
    parts = []
    n = 0
    for c, _depth in _walk(w):
        n += 1
        if n > limit:
            break
        try:
            if c.ControlTypeName in ("TextControl", "DocumentControl", "ListItemControl") and c.Name:
                parts.append(c.Name)
        except Exception:
            pass
    return "\n".join(parts)


def _walk(w, max_depth=40):
    ua = _ua()
    return ua.WalkControl(w, includeTop=False, maxDepth=max_depth)


def input_box(w):
    best = None
    for c, _d in _walk(w):
        try:
            if c.ControlTypeName == "EditControl":
                best = c  # the chat input is the last editable field in the tree
        except Exception:
            pass
    return best


def cmd_windows():
    ua = _ua()
    ws = []
    for w in ua.GetRootControl().GetChildren():
        try:
            if w.Name:
                ws.append(f"{w.ControlTypeName}|{w.ClassName}|{w.Name}")
        except Exception:
            pass
    out(ok=True, windows=ws)


def cmd_tree(key, n=4000):
    w = find_window(key)
    if not w:
        return out(ok=False, error="אין חלון")
    rows = []
    for c, d in _walk(w):
        try:
            rows.append(f"{d}|{c.ControlTypeName}|{c.ClassName}|{(c.Name or '')[:60]}")
        except Exception:
            pass
        if len(rows) >= n:
            break
    out(ok=True, tree=rows)


def bring_front(w):
    """Windows only lets a process take the foreground right after input: a tap on Alt first (the usual workaround)."""
    import ctypes
    u, h = ctypes.windll.user32, w.NativeWindowHandle
    if u.IsIconic(h):
        u.ShowWindow(h, 9)
    for _ in range(3):
        u.keybd_event(0x12, 0, 0, 0)
        u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(h)
        time.sleep(0.6)
        if u.GetForegroundWindow() == h:
            return True
    return False


def send_button(w):
    rx = re.compile(r"^(Send|Send message|Submit|שלח|שליחה)", re.I)
    found = None
    for c, _d in _walk(w):
        try:
            if c.ControlTypeName == "ButtonControl" and rx.search(_clean(c.Name)):
                found = c
        except Exception:
            pass
    return found


def cmd_list():
    res = {k: bool(find_window(k)) for k in APPS}
    out(ok=True, running=[k for k, v in res.items() if v], known=list(APPS))


def cmd_has(key):
    """Cheap check for the bridge (never types): the window is open, or the app is installed and can be opened."""
    running = bool(find_window(key))
    installed = running or bool(app_id(key))
    out(ok=installed, app=key, running=running, installed=installed,
        **({} if installed else {"error": "האפליקציה לא מותקנת במחשב הזה"}))


def cmd_ask(key, question, timeout):
    ua = _ua()
    w = find_window(key)
    aid = None if w else app_id(key)
    if aid:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + aid])
        for _ in range(30):
            time.sleep(1)
            w = find_window(key)
            if w:
                break
        time.sleep(4)
    if not w:
        return out(ok=False, app=key, error="החלון של האפליקציה לא נמצא (לא מותקנת או לא נפתחה)")
    box = input_box(w)
    if not box:
        return out(ok=False, app=key, error="לא נמצאה תיבת ההקלדה בחלון")
    before = window_text(w)
    how = ""
    if bring_front(w):
        box.SetFocus()  # UIA focus, no mouse click (a click lands on whatever window is on top there)
        time.sleep(0.4)
        if ua.GetForegroundControl().NativeWindowHandle == w.NativeWindowHandle:
            ua.SetClipboardText(question)
            ua.SendKeys("{Ctrl}v", waitTime=0.3)
            ua.SendKeys("{Enter}", waitTime=0.5)
            how = "keys"
    if not how:
        # Windows refused the foreground (owner working in another window): fill the field through UI Automation and
        # press the app's send button, without touching the keyboard or the owner's window
        try:
            box.GetValuePattern().SetValue(question)
        except Exception as e:
            return out(ok=False, app=key, error=f"החלון לא עבר לחזית ואי אפשר למלא את התיבה ברקע ({e})",
                       foreground=_clean(ua.GetForegroundControl().Name))
        time.sleep(0.8)
        btn = send_button(w)
        if not btn:
            return out(ok=False, app=key, error="מילאתי את השאלה ברקע אבל לא מצאתי כפתור שליחה")
        if not btn.IsEnabled:  # desktop 18.11: ChatGPT's page does not see text set from outside (a posted space did not help either)
            return out(ok=False, app=key, error="החלון לא עבר לחזית, ומילוי התיבה ברקע לא הפעיל את כפתור השליחה של האפליקציה")
        btn.GetInvokePattern().Invoke()
        how = "uia"
    t0, last, stable_since = time.time(), before, None
    while time.time() - t0 < timeout:
        time.sleep(2)
        now = window_text(w)
        if now != last:
            last, stable_since = now, time.time()
        elif now != before and stable_since and time.time() - stable_since >= 8:
            break
    answer = _new_part(before, last, question)
    if not answer:
        return out(ok=False, app=key, error="לא זוהתה תשובה בזמן", waited=int(time.time() - t0))
    out(ok=True, app=key, answer=answer, seconds=int(time.time() - t0), how=how)


NOISE = re.compile(r"^(just now|now|\d+ ?(s|sec|m|min|h) ago|you.ve used|of your .*limit|\d+%|.* is ai and can make mistakes\.?|"
                   r".*finished the response|.*is thinking.*|low|medium|high|copy|retry|edit|share|"
                   r"(claude )?(opus|sonnet|haiku|fable)( \d[\d.]*)?|gpt[- ]?\S*|stop|regenerate|"
                   # Jan (desktop 19.11): its message stamps, speed and context meter came back as the answer
                   r"[a-z]{3} \d{1,2}, \d{4},? \d{1,2}:\d{2}( ?[ap]m)?|tokens/sec|\(|\)|\(?(\d+ )?tokens\)?|[\d.]+ ?%?|%|"
                   r"reading: \d+%|[\d.]+k? / [\d.]+k? tokens|ask for approval)$", re.I)


def _new_part(before, after, question):
    """The answer = what was added after our question, without the app's own labels (time, model, usage, buttons)."""
    tail = after[len(before):] if after.startswith(before) else after
    q = question.strip().splitlines()[0][:60] if question.strip() else ""
    if q and q in tail:
        tail = tail[tail.rfind(q) + len(q):]
    said = [ln.split("responded:", 1)[1].strip() for ln in tail.splitlines() if "responded:" in ln]
    if said:  # Claude Desktop labels the answer for screen readers: "Claude responded: ..."
        return said[-1]
    if q and q not in after:  # our question is not in the conversation (salon 18.11: an identity-check dialog covered ChatGPT, and
        return ""             # its whole window text came back as if it were the answer)
    lines, prev = [], None
    for ln in tail.splitlines():
        t = ln.strip()
        if not t or t in question or NOISE.match(t) or t == prev:
            continue
        lines.append(t)
        prev = t
    return "\n".join(lines).strip()


def via_task(argv):
    """Re-run in the interactive user session (from ssh) and relay the output."""
    # the ssh session's %TEMP% is not reachable the same way from the desktop session (desktop 18.11): a shared folder
    base = os.path.join(os.environ.get("PUBLIC") or "C:/Users/Public", "desktop_ai")
    os.makedirs(base, exist_ok=True)
    # not mkdtemp: its 0o700 folder (Python 3.13+) made from an elevated ssh session is not readable by the desktop session
    tmp = os.path.join(base, f"dai_{os.getpid()}_{int(time.time())}")
    os.makedirs(tmp, exist_ok=True)
    res = os.path.join(tmp, "out.json")
    # pythonw + --out: no console window pops up on the owner's screen (a cmd window would take the foreground)
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    py = pyw if os.path.exists(pyw) else sys.executable
    # the arguments go in a file: schtasks refuses a /tr longer than 261 characters (lap 18.11: every `ask` failed silently on it)
    json.dump(argv + ["--out", res], open(os.path.join(tmp, "args.json"), "w", encoding="utf-8"), ensure_ascii=False)
    tr = " ".join(f'"{a}"' for a in [py, "-X", "utf8", os.path.abspath(__file__), "--task-args", os.path.join(tmp, "args.json")])
    tn = "DesktopAI_" + str(os.getpid())
    c = subprocess.run(["schtasks", "/create", "/tn", tn, "/tr", tr, "/sc", "once", "/st", "23:59", "/it", "/f"],
                       capture_output=True, text=True, errors="replace")
    if c.returncode != 0:
        return out(ok=False, error="לא הצלחתי ליצור משימה בסשן המשתמש: " + ((c.stderr or c.stdout or "").strip()[-300:]))
    subprocess.run(["schtasks", "/run", "/tn", tn], capture_output=True)
    limit = 60 + int(dict(zip(argv, argv[1:])).get("--timeout", 180))
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(2)
        try:
            txt = open(res, encoding="utf-8").read()
            if txt.strip().endswith("}"):
                break
        except OSError:
            pass
    subprocess.run(["schtasks", "/delete", "/tn", tn, "/f"], capture_output=True)
    try:
        txt = open(res, encoding="utf-8", errors="replace").read().strip()
        print(txt if txt.endswith("}") else json.dumps({"ok": False, "error": "הפלט מהסשן לא תקין", "raw": txt[-1500:]}, ensure_ascii=False))
    except OSError:
        out(ok=False, error="המשימה בסשן המשתמש לא החזירה תשובה (אין משתמש מחובר למחשב?)")


def stdin_question(argv):
    """`ask <app> -`: the question comes on stdin; saved to a file the desktop-session run can read (the --via-task run has no stdin)."""
    if len(argv) >= 3 and argv[0] == "ask" and argv[2] == "-":
        base = os.path.join(os.environ.get("PUBLIC") or "C:/Users/Public", "desktop_ai")
        os.makedirs(base, exist_ok=True)
        f = os.path.join(base, f"q_{os.getpid()}_{int(time.time())}.txt")
        data = sys.stdin.buffer.read().decode("utf-8", "replace")
        open(f, "w", encoding="utf-8").write(data)
        argv = argv[:2] + [f] + argv[3:]
    return argv


def main(argv):
    if argv[:1] == ["--task-args"]:  # the run inside the user's session (via_task)
        argv = json.load(open(argv[1], encoding="utf-8"))
    argv = stdin_question(argv)
    if "--out" in argv and "--via-task" not in argv:
        i = argv.index("--out")
        sys.stdout = open(argv[i + 1], "w", encoding="utf-8")
        sys.stderr = sys.stdout
        argv = argv[:i] + argv[i + 2:]
    if "--via-task" in argv:
        return via_task([a for a in argv if a != "--via-task"])
    if argv and argv[0] == "tree":
        return cmd_tree(argv[1].lower())
    if argv and argv[0] == "windows":
        return cmd_windows()
    if argv and argv[0] == "has" and len(argv) >= 2:
        key = argv[1].lower()
        return cmd_has(key) if key in APPS else out(ok=False, error=f"לא מכיר את {key}")
    if not argv or argv[0] == "list":
        return cmd_list()
    if argv[0] == "ask" and len(argv) >= 3:
        key = argv[1].lower()
        if key not in APPS:
            return out(ok=False, error=f"לא מכיר את {key}. אפשרויות: {', '.join(APPS)}")
        q = open(argv[2], encoding="utf-8").read()
        if os.path.basename(argv[2]).startswith("q_") and os.path.basename(os.path.dirname(argv[2])) == "desktop_ai":
            try:
                os.remove(argv[2])  # a stdin question: read once
            except OSError:
                pass
        to = int(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else 180
        return cmd_ask(key, q, to)
    print(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as e:  # always one JSON line, also from the hidden desktop-session run
        import traceback
        tb = traceback.extract_tb(e.__traceback__)
        out(ok=False, error=f"{type(e).__name__}: {e}", at=" < ".join(f"{f.name}:{f.lineno}" for f in reversed(tb[-3:])))
    finally:
        sys.stdout.flush()
