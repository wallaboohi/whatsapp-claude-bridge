"""The fixes in the pipe before a restart (owner 20.11 12:17): one list that every session, provider and computer working on the
bridge's code writes to, so parallel work is never overwritten or lost, plus a detailed log.

    pending.py add "<what>" --by "<group / provider>" [--files a.py,b.go]   -> new entry (status: בעבודה), prints its id
    pending.py check a.py,b.go [--by ...]                                   -> who else is working on these files now
    pending.py commit <id> <sha> [<sha>...]                                 -> saved in git (status: שמור, waits for the restart)
    pending.py drop <id> "<why>"                                            -> dropped (kept in the log)
    pending.py live                                                         -> entries whose commits the desktop now runs -> פעיל
    pending.py list [--all]                                                 -> the list + what git says: commits not running yet,
                                                                               uncommitted files in the working tree

Data: agent/pending.json (this computer, not in git); every change is appended to agent/pending.log.
"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "agent", "pending.json")
LOG = os.path.join(ROOT, "agent", "pending.log")
OPEN = ("בעבודה", "שמור")


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def load():
    try:
        return json.load(open(DATA, encoding="utf-8"))
    except (OSError, ValueError):
        return {"next": 1, "items": []}


def save(d, line):
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now()} {line}\n")


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.rstrip()


def running_head():
    try:
        db = sqlite3.connect("file:" + os.path.join(ROOT, "agent", "agent.db") + "?mode=ro", uri=True, timeout=10)
        r = db.execute("SELECT v FROM kv WHERE k = 'running_head'").fetchone()
        return r[0] if r else ""
    except sqlite3.Error:
        return ""


def opt(a, name, default=""):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def files_of(s):
    return [f.strip().replace("\\", "/") for f in (s or "").split(",") if f.strip()]


def item(d, i):
    for it in d["items"]:
        if str(it["id"]) == str(i).lstrip("#"):
            return it
    sys.exit(f"אין פריט #{i}")


def overlaps(d, files, by=""):
    out = []
    for it in d["items"]:
        if it["status"] in OPEN and it.get("by") != by:
            common = sorted(set(files) & set(it.get("files") or []))
            if common:
                out.append(f"#{it['id']} ({it.get('by') or '?'}, {it['status']}): {', '.join(common)} — {it['what'][:70]}")
    return out


def show(d, all_=False):
    rows = [it for it in d["items"] if all_ or it["status"] in OPEN]
    print(f"🧺 תיקונים בקנה לפני הפעלה מחדש ({len(rows)}):")
    for it in rows:
        c = " ".join(it.get("commits") or [])
        print(f"#{it['id']} [{it['status']}] {it['what']} · {it.get('by') or '?'} · {it['at'][5:16]}"
              + (f" · קבצים: {', '.join(it['files'])}" if it.get("files") else "") + (f" · קומיטים: {c}" if c else ""))
    rh = running_head()
    hub = git("rev-parse", "hub/main") or git("rev-parse", "HEAD")
    if rh and hub:
        commits = git("log", "--format=%h %s", f"{rh}..{hub}").splitlines()
        print(f"\n📦 קומיטים שעוד לא רצים בנייח (רץ: {rh[:7]}, hub: {hub[:7]}): {len(commits)}")
        listed = {c for it in d["items"] for c in (it.get("commits") or [])}
        for ln in commits:
            sha = ln.split(" ", 1)[0]
            mark = "" if any(sha.startswith(x[:7]) or x.startswith(sha) for x in listed) else "  ⚠️ לא ברשימה"
            print(f"  {ln[:110]}{mark}")
    dirty = [ln for ln in git("status", "--porcelain", "--untracked-files=no").splitlines() if ln.strip()]
    if dirty:
        print(f"\n✏️ שינויים לא שמורים בעץ העבודה ({len(dirty)}): " + ", ".join(ln[3:] for ln in dirty))


def main(a):
    if not a or a[0] in ("list", "ls"):
        show(load(), "--all" in a)
        return
    d, cmd = load(), a[0]
    if cmd == "add":
        what, by, files = a[1], opt(a, "--by"), files_of(opt(a, "--files"))
        warn = overlaps(d, files, by)
        it = {"id": d["next"], "what": what, "by": by, "files": files, "status": "בעבודה", "at": now(), "commits": []}
        d["next"] += 1
        d["items"].append(it)
        save(d, f"add #{it['id']} by={by} files={','.join(files)} :: {what}")
        print(f"#{it['id']} נוסף")
        for w in warn:
            print("⚠️ עובדים גם על:", w)
    elif cmd == "check":
        warn = overlaps(d, files_of(a[1]), opt(a, "--by"))
        print("\n".join("⚠️ " + w for w in warn) if warn else "✅ אף אחד אחר לא רשום על הקבצים האלה")
    elif cmd == "commit":
        it = item(d, a[1])
        it["commits"] = list(dict.fromkeys((it.get("commits") or []) + a[2:]))
        it["status"] = "שמור"
        save(d, f"commit #{it['id']} {' '.join(a[2:])}")
        print(f"#{it['id']} שמור, ממתין להפעלה מחדש")
    elif cmd == "drop":
        it = item(d, a[1])
        it["status"], it["why"] = "בוטל", (a[2] if len(a) > 2 else "")
        save(d, f"drop #{it['id']} :: {it['why']}")
        print(f"#{it['id']} בוטל")
    elif cmd == "live":
        rh = running_head()
        n = 0
        for it in d["items"]:
            if it["status"] == "שמור" and rh and it.get("commits") and all(
                    subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor", c, rh]).returncode == 0 for c in it["commits"]):
                it["status"], it["live_at"] = "פעיל", now()
                n += 1
        save(d, f"live {n} (running {rh[:7]})")
        print(f"{n} פריטים פעילים עכשיו")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
