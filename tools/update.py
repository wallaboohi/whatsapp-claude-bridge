"""Update an installed YB AI Bridge for WhatsApp to the latest release, with automatic rollback.

  update.cmd                 update to the latest GitHub release (asks nothing)
  update.cmd --check         only report whether an update exists (JSON on the last line)
  update.cmd --to <ref>      update to a specific tag / commit (testing)
  update.cmd --simulate-fail force the post-update self-test to fail (tests the rollback)

Personal files (config, agent/home, store, databases, logs) are git-ignored and never touched.
Flow: stop services → remember the current version → check out the new tag → new bridge binary → dependencies →
dashboard app → start services → self-test → on failure roll everything back and start the old version again.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "WallaBoohi/whatsapp-claude-bridge"
BRIDGE_EXE = os.path.join(ROOT, "whatsapp-bridge", "whatsapp-bridge.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOG = os.path.join(ROOT, "agent", "update.log")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ver(v):
    return tuple(int(x) for x in v.lstrip("v").split("."))


def current_version():
    return open(os.path.join(ROOT, "VERSION")).read().strip()


def latest_release():
    """Latest published release: tag, notes, assets."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers={"User-Agent": "wa-claude-updater"})
    r = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    return {"tag": r["tag_name"], "version": r["tag_name"].lstrip("v"), "notes": r.get("body") or "",
            "assets": {a["name"]: a["browser_download_url"] for a in r.get("assets", [])}}


def check():
    cur = current_version()
    try:
        rel = latest_release()
    except Exception as e:
        return {"available": False, "current": cur, "error": str(e)}
    return {"available": ver(rel["version"]) > ver(cur), "current": cur, "latest": rel["version"], "notes": rel["notes"][:2000]}


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=check).stdout.strip()


def uv():
    return shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")


def ps(cmd):
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)


def stop_services():
    # stop the run-*.cmd loops first (so nothing restarts mid-update), then the processes they run
    ps("Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -like '*run-bridge.cmd*' -or "
       "$_.CommandLine -like '*run-agent.cmd*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    ps("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'whatsapp-bridge.exe' -or ($_.CommandLine -like '*agent.py*' -and "
       "$_.CommandLine -notlike '*update.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    time.sleep(3)


def start_services():
    for name in ("bridge", "agent"):
        subprocess.Popen(["conhost.exe", "--headless", "cmd.exe", "/c", os.path.join(ROOT, f"run-{name}.cmd")], creationflags=NO_WINDOW)
        time.sleep(20 if name == "bridge" else 5)


def sync_deps():
    cfg = {}
    try:
        cfg = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
    except Exception:
        pass
    extras = ["--extra", "plugins"] + (["--extra", "voice"] if cfg.get("transcribe", True) else [])
    subprocess.run([uv(), "sync", "-q"] + extras, cwd=os.path.join(ROOT, "agent"), check=True)
    subprocess.run([uv(), "sync", "-q"], cwd=os.path.join(ROOT, "whatsapp-mcp-server"), check=True)


def new_bridge(assets):
    """Build locally when Go + gcc exist (developers), otherwise download the release binary."""
    go, gcc = shutil.which("go"), shutil.which("gcc")
    tmp = BRIDGE_EXE + ".new"
    if go and gcc:
        subprocess.run([go, "build", "-ldflags", f"-X main.version={current_version()}", "-o", tmp, "."],
                       cwd=os.path.dirname(BRIDGE_EXE), check=True, env={**os.environ, "CGO_ENABLED": "1"})
    elif "whatsapp-bridge.exe" in assets:
        urllib.request.urlretrieve(assets["whatsapp-bridge.exe"], tmp)
    else:
        return False
    os.replace(tmp, BRIDGE_EXE)
    return True


def update_dashboard(assets):
    rel = os.path.join(ROOT, "releases")
    os.makedirs(rel, exist_ok=True)
    if "latest.json" not in assets:
        return
    info = json.loads(urllib.request.urlopen(assets["latest.json"], timeout=15).read().decode())
    if info.get("file") in assets:
        urllib.request.urlretrieve(assets[info["file"]], os.path.join(rel, info["file"]))
        json.dump(info, open(os.path.join(rel, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False)
        ps(f"Get-Process WhatsAppDash -ErrorAction SilentlyContinue | Stop-Process -Force")
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "tools", "install_dash.ps1")],
                       capture_output=True, creationflags=NO_WINDOW)


def selftest(simulate_fail):
    if simulate_fail:
        return False
    out = subprocess.run([uv(), "run", "-q", "--no-sync", "python", "-X", "utf8", os.path.join(ROOT, "tools", "selftest.py"), "--quiet"],
                         cwd=os.path.join(ROOT, "agent"), capture_output=True, text=True, encoding="utf-8")
    log(out.stdout[-1500:])
    res = json.load(open(os.path.join(ROOT, "agent", "selftest.json"), encoding="utf-8"))
    # the dashboard-app check depends on the machine, everything else must pass
    return all(r["ok"] for r in res if r["name"] != "אפליקציה (EXE)")


def main():
    args = sys.argv[1:]
    if "--check" in args:
        print(json.dumps(check(), ensure_ascii=False))
        return 0
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        log("✖ העדכון האוטומטי דורש התקנה עם git clone. / Auto-update needs a git clone install.")
        return 1
    if git("status", "--porcelain", "--untracked-files=no"):
        log("✖ יש שינויים בקבצי הכלי (מצב מפתח) — עדכן ידנית עם git. / Tracked files are modified (developer mode).")
        return 1
    target = args[args.index("--to") + 1] if "--to" in args else None
    assets = {}
    if not target:
        rel = latest_release()
        if ver(rel["version"]) <= ver(current_version()):
            log(f"✔ הגרסה עדכנית ({current_version()}). / Up to date.")
            return 0
        target, assets = rel["tag"], rel["assets"]
    old_ref, old_ver = git("rev-parse", "HEAD"), current_version()
    old_branch = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    shutil.copy(BRIDGE_EXE, BRIDGE_EXE + ".bak") if os.path.exists(BRIDGE_EXE) else None
    log(f"מעדכן {old_ver} ← {target} / updating {old_ver} → {target}")
    stop_services()
    ok = False
    try:
        git("fetch", "--tags", "--quiet", "origin", check=False)
        if old_branch:  # stay on the branch (developer machines keep working on it): fast-forward only
            git("merge", "--ff-only", "--quiet", target)
        else:
            git("checkout", "--quiet", target)
        new_bridge(assets)
        sync_deps()
        update_dashboard(assets)
        start_services()
        ok = selftest("--simulate-fail" in args)
    except Exception as e:
        log(f"✖ שגיאה בעדכון / update error: {e}")
    if ok:
        log(f"✔ עודכן לגרסה {current_version()} / updated to {current_version()}")
        if os.path.exists(BRIDGE_EXE + ".bak"):
            os.remove(BRIDGE_EXE + ".bak")
        return 0
    log("↩ הבדיקה נכשלה — חוזר לגרסה הקודמת / self-test failed — rolling back")
    stop_services()
    if old_branch:
        git("checkout", "--quiet", old_branch)
        git("reset", "--hard", "--quiet", old_ref)  # undo the fast-forward (tracked files were clean before the update)
    else:
        git("checkout", "--quiet", old_ref)
    if os.path.exists(BRIDGE_EXE + ".bak"):
        os.replace(BRIDGE_EXE + ".bak", BRIDGE_EXE)
    try:
        sync_deps()
    except Exception as e:
        log(f"sync after rollback: {e}")
    start_services()
    log(f"↩ הוחזר לגרסה {old_ver} / restored {old_ver}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
