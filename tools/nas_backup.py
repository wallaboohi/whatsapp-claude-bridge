"""The bridge's full daily backup to the TrueNAS (owner 21.11 20:45: "everything, from every place — the only backup of the bridge and
its content").

Runs outside the agent (Task Scheduler, daily 02:30), so it works when the agent is down. The owner's rule (21.11 23:10):
  every day   the code with its history (git push to "nas", a new dated branch, never forced) and a clean copy of the code
              (git archive: only what git keeps) as <NAS>/<PC>/code-daily/<date>.zip — kept 30 days, the older zips deleted
  every 7 days a full image, code included: <NAS>/<PC>/full/<date>/ = every file of the bridge folder (store / media, secrets,
              config, agent/home, provider folders ...), a consistent copy of every SQLite database (sqlite backup API) and the
              bridge's data outside its folder (_outside: the CLIs' settings, the bridge's Claude memory). Full images are kept.
The result: agent/nas_backup.log and agent/nas_backup_status.json (the agent reports a failure in the bridge-log group).

  python tools/nas_backup.py [--dest \\\\host\\share\\dir] [--full]
"""
import datetime as dt
import glob
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEST_DEFAULT = r"\\10.0.0.154\YAKIR\bridge-backup"
SKIP_DIRS = [".venv", "node_modules", "__pycache__", ".pytest_tmp", "browser_profile", "tmp", ".aider.tags.cache.v4"]
SMALL_DIRS = ["secrets", os.path.join("agent", "home")]
SMALL_GLOBS = [os.path.join("agent", "*.json"), os.path.join("whatsapp-bridge", "store", "*.json"), os.path.join("agent", "providers", "**", "*")]
BACKUP_EXTRA = [os.path.join(HOME, ".codex", "config.toml"), os.path.join(HOME, ".gemini", "settings.json"),
                os.path.join(HOME, ".qwen", "settings.json"), os.path.join(HOME, ".claude", "projects", "C--WINDOWS-system32", "memory")]
LOG = os.path.join(ROOT, "agent", "nas_backup.log")
STATUS = os.path.join(ROOT, "agent", "nas_backup_status.json")


def log(*a):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run(cmd, cwd=ROOT, timeout=3600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=NO_WINDOW)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def databases():
    """Every SQLite database of the bridge (not caches, not test leftovers)."""
    found = []
    for pat in (os.path.join(ROOT, "agent", "*.db"), os.path.join(ROOT, "whatsapp-bridge", "store", "*.db"), os.path.join(ROOT, "*.db")):
        found += glob.glob(pat)
    return sorted(set(found))


def snapshot_db(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def copy_tree(src, dst):
    """robocopy /E (copy and update, never delete at the destination). Exit codes below 8 are success."""
    xd = []
    for d in SKIP_DIRS:
        xd += ["/XD", d]
    rc, out = run(["robocopy", src, dst, "/E", "/R:2", "/W:5", "/MT:8", "/NP", "/NFL", "/NDL", "/NJH",
                   "/XF", "*.db", "*.db-wal", "*.db-shm", "*.db-journal", *xd], timeout=7200)
    summary = " ".join(l.strip() for l in out.splitlines() if l.strip().startswith(("Dirs", "Files", "Bytes")))[:300]
    return rc < 8, f"robocopy {rc}: {summary}"


CODE_KEEP_DAYS = 30  # owner 21.11 23:10: a clean code copy every day, kept 30 days, the older ones deleted
FULL_EVERY_DAYS = 7  # owner 21.11 23:10: every 7 days everything, code included — a full image


def code_daily(base, day):
    """A clean copy of the code (only what git keeps: no secrets, no data) as <base>/code-daily/<day>.zip; copies older than
    CODE_KEEP_DAYS are deleted (the owner's own rule; only this folder, only these dated zips)."""
    folder = os.path.join(base, "code-daily")
    os.makedirs(folder, exist_ok=True)
    rc, out = run(["git", "archive", "--format=zip", "-o", os.path.join(folder, f"{day}.zip"), "HEAD"], timeout=600)
    if rc:
        return "failed: " + out.strip()[-200:]
    cut = dt.date.today() - dt.timedelta(days=CODE_KEEP_DAYS)
    gone = 0
    for f in glob.glob(os.path.join(folder, "????-??-??.zip")):
        try:
            if dt.date.fromisoformat(os.path.basename(f)[:10]) < cut:
                os.remove(f)
                gone += 1
        except (ValueError, OSError):
            pass
    return f"ok ({day}.zip" + (f", {gone} older than {CODE_KEEP_DAYS} days deleted)" if gone else ")")


def full_due(base):
    """True when the newest full image is FULL_EVERY_DAYS old or more (or there is none)."""
    days = []
    for d in glob.glob(os.path.join(base, "full", "????-??-??")):
        try:
            days.append(dt.date.fromisoformat(os.path.basename(d)))
        except ValueError:
            pass
    return not days or (dt.date.today() - max(days)).days >= FULL_EVERY_DAYS


def main(dest, force_full=False):
    t0 = time.time()
    host = socket.gethostname()
    base = os.path.join(dest, host)
    day = dt.date.today().isoformat()
    status = {"at": dt.datetime.now().isoformat(timespec="seconds"), "dest": base, "steps": {}, "ok": False}
    share = os.path.dirname(dest.rstrip("\\/"))
    try:
        if os.path.isdir(share):
            os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    if not os.path.isdir(base):
        status["steps"]["nas"] = f"not reachable: {dest}"
        log("NAS not reachable:", dest)
        json.dump(status, open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return 2
    # 1. the code, with its history
    # a new branch every day on the NAS (never forced, never overwritten): its main had gone apart from this one (19.11), and a
    # backup must not rewrite what the NAS already keeps
    ref = f"refs/heads/backup/{host}/{day}"
    if run(["git", "ls-remote", "--exit-code", "nas", ref], timeout=120)[0] == 0:
        ref += f"-{dt.datetime.now():%H%M}"
    rc, out = run(["git", "push", "-q", "nas", f"HEAD:{ref}"], timeout=600)
    rc2, out2 = run(["git", "push", "-q", "nas", "--tags"], timeout=600)
    status["steps"]["code"] = "ok" if rc == 0 and rc2 == 0 else ("failed: " + (out + out2).strip().splitlines()[-1][:200] if (out + out2).strip() else "failed")
    log("code (git push nas):", status["steps"]["code"])
    # 2. every day: a clean copy of the code, kept 30 days
    status["steps"]["code_daily"] = code_daily(base, day)
    log("clean code copy:", status["steps"]["code_daily"])
    code_ok = status["steps"]["code"] == "ok" and status["steps"]["code_daily"].startswith("ok")
    if not (force_full or full_due(base)):
        status["steps"]["full"] = "not due (every %d days)" % FULL_EVERY_DAYS
        status["ok"] = code_ok
        status["seconds"] = int(time.time() - t0)
        json.dump(status, open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log("done in", status["seconds"], "s, ok:", status["ok"], "(no full image today)")
        return 0 if status["ok"] else 1
    # 3. every 7 days: the full image -> <base>/full/<day> (consistent database copies, every file, the data outside the folder)
    full = os.path.join(base, "full", day)
    stage = tempfile.mkdtemp(prefix="yb-nas-")
    dbs = []
    for src in databases():
        rel = os.path.relpath(src, ROOT)
        try:
            snapshot_db(src, os.path.join(stage, rel))
            dbs.append(rel)
        except Exception as e:
            log("database copy failed:", rel, e)
    status["steps"]["databases"] = f"{len(dbs)} copied"
    log("databases:", ", ".join(dbs))
    ok, note = copy_tree(ROOT, full)  # every file but the databases (copied consistently above) and rebuildable caches
    status["steps"]["files"] = note if ok else "failed: " + note
    log("files:", note)
    for rel in dbs:
        dst = os.path.join(full, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(stage, rel), dst)
    extra = 0
    for src in BACKUP_EXTRA:  # the bridge's data outside its folder
        if not os.path.exists(src):
            continue
        dst = os.path.join(full, "_outside", os.path.relpath(src, HOME))
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        extra += 1
    status["steps"]["outside"] = f"{extra} items"
    status["steps"]["full"] = full
    shutil.rmtree(stage, ignore_errors=True)  # our own temporary copies
    status["ok"] = code_ok and ok and bool(dbs)
    status["seconds"] = int(time.time() - t0)
    json.dump(status, open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("done in", status["seconds"], "s, ok:", status["ok"])
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    d = DEST_DEFAULT
    if "--dest" in sys.argv:
        d = sys.argv[sys.argv.index("--dest") + 1]
    sys.exit(main(d, force_full="--full" in sys.argv))
