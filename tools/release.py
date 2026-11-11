"""Release a new version of the WhatsApp ↔ Claude tool (maintainer).

  release.cmd 1.1.0 "מה השתנה / what changed"            run all checks, commit, tag and push → GitHub builds and publishes
  release.cmd 1.1.0 "…" --dry-run                         everything except commit/tag/push

1. bumps the version everywhere: VERSION, dash/VERSION, .claude-plugin/plugin.json, agent/pyproject.toml
2. adds the notes on top of CHANGELOG.md
3. checks: Go vet + tests, agent tests, MCP import, `claude plugin validate --strict`, no personal data in tracked files
4. commits "Release vX.Y.Z", tags vX.Y.Z, pushes main + tag (GitHub Actions builds the bridge and the dashboard
   and publishes the release; installed tools see it in the dashboard and get a WhatsApp notice the next morning)
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, cwd=ROOT, env=None):
    print("→", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if r.returncode:
        out = (r.stdout or "") + (r.stderr or "")
        print(out[-2000:])
        failed = [l for l in out.splitlines() if l.startswith(("FAILED", "ERROR"))]
        if failed:
            print("\n".join(failed[:5]))
        sys.exit(f"✖ נכשל: {' '.join(cmd)}")
    return r.stdout


def rewrite(rel, fn):
    path = os.path.join(ROOT, *rel.split("/"))
    with open(path, encoding="utf-8", newline="") as f:
        old = f.read()
    new = fn(old)
    if new != old:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)


RISKY = (".xlsx", ".xls", ".csv", ".docx", ".doc", ".pdf", ".jpg", ".jpeg", ".heic", ".mp4", ".ogg", ".opus", ".db", ".sqlite",
         ".json.bak", ".zip")
# files in the repo that are allowed to have a "risky" extension (part of the tool itself)
RISKY_OK = ()


def personal_data_scan(patterns):
    files = run(["git", "ls-files"]).split()
    hits = []
    for f in files:
        if f.endswith((".woff2", ".ico", ".png", ".exe", ".sum", ".lock")) or "chart.umd" in f:
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        hits += [f"{f}: {p}" for p in patterns if p and p in text]
    return hits


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("שימוש: release.cmd X.Y.Z \"מה השתנה\" [--dry-run]")
    ver, notes, dry = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ""), "--dry-run" in sys.argv
    for a in sys.argv[2:]:  # --notes-file=<utf8 file>: notes without passing Hebrew through the command line
        if a.startswith("--notes-file="):
            notes = open(a.split("=", 1)[1], encoding="utf-8").read().strip()
    if run(["git", "status", "--porcelain", "--untracked-files=no"]).strip() and not dry:
        sys.exit("✖ יש שינויים שלא נשמרו (commit) — שמור אותם קודם.")

    # 1. version everywhere (read before writing; keep each file's line endings)
    for rel in ("VERSION", "dash/VERSION"):
        rewrite(rel, lambda t: re.sub(r"^\S+", ver, t, count=1))
    rewrite(".claude-plugin/plugin.json", lambda t: re.sub(r'("version"\s*:\s*")[^"]*"', rf'\g<1>{ver}"', t, count=1))
    rewrite("agent/pyproject.toml", lambda t: re.sub(r'(?m)^version = ".*"', f'version = "{ver}"', t, count=1))
    # uv.lock carries the project version too; left stale, every `uv run` rewrites it and the dirty tree blocks the
    # nightly save / release (owner 11.11)
    rewrite("agent/uv.lock", lambda t: re.sub(r'(name = "whatsapp-claude-agent"\r?\nversion = ")[^"]*"', rf'\g<1>{ver}"', t, count=1))

    # 2. changelog
    entry = f"## {ver} — {dt.date.today().isoformat()}\n{notes or '—'}\n\n"
    rewrite("CHANGELOG.md", lambda t: t if f"## {ver} " in t else
            re.sub(r"# Changelog\r?\n\r?\n", lambda m: m.group(0) + entry, t, count=1))

    # 3. checks
    go = shutil.which("go") or "C:/Program Files/Go/bin/go.exe"
    env = {"CGO_ENABLED": "1", "PATH": "C:/msys64/ucrt64/bin;" + os.environ["PATH"]}
    run([go, "vet", "."], cwd=os.path.join(ROOT, "whatsapp-bridge"), env=env)
    run([go, "test", "./..."], cwd=os.path.join(ROOT, "whatsapp-bridge"), env=env)
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
    # owner 10.11: a test that passed only at some hours broke the 23:30 release — run the suite now and at fake clock
    # times too (night/quiet hours, morning edge, release time, weekend, year end; agent/tests/conftest.py FAKE_NOW)
    for when in ("", "2026-11-10 02:00", "2026-11-10 07:00", "2026-11-10 12:00", "2026-11-10 23:30",
                 "2026-11-13 15:00", "2026-11-14 23:59", "2026-12-31 23:59"):
        if when:
            print("  🕒 שעון מדומה:", when)
        run([uv, "run", "-q", "--no-sync", "--group", "dev", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            cwd=os.path.join(ROOT, "agent"), env={"FAKE_NOW": when} if when else None)
    run([uv, "run", "-q", "python", "-c", "import main, extras"], cwd=os.path.join(ROOT, "whatsapp-mcp-server"))
    claude = shutil.which("claude")
    if claude:
        run([claude, "plugin", "validate", "--strict", "."])
    cfg = {}
    try:
        cfg = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
    except Exception:
        pass
    personal = [cfg.get("claude_group", ""), os.path.expanduser("~").replace("\\", "/"), os.path.expanduser("~")]
    try:
        pol = json.load(open(os.path.join(ROOT, "whatsapp-bridge", "store", "send_policy.json"), encoding="utf-8"))
        personal += [o.split("@")[0] for o in pol.get("owner", [])]
    except Exception:
        pass
    # photos / Excel / PDF are not text-scanned: list them and ASK (the owner decides) — continue with --allow-files
    files = [f for f in run(["git", "ls-files"]).split() if f.lower().endswith(RISKY) and f not in RISKY_OK]
    if files and "--allow-files" not in sys.argv:
        sys.exit("❓ יש בגיט קבצים שכדאי לבדוק לפני פרסום (תמונה / אקסל / PDF):\n" + "\n".join(files)
                 + "\nמה לעשות? להסיר אותם מהגיט, או להריץ שוב עם --allow-files כדי לפרסם איתם.")
    hits = personal_data_scan([x for x in personal if len(x) > 6])
    if hits:
        sys.exit("✖ נמצא מידע אישי בקבצים שעולים לגיט:\n" + "\n".join(hits))
    print("✔ כל הבדיקות עברו")
    bumped = BUMPED
    if dry:
        run(["git", "checkout", "--"] + bumped)
        print("(dry run — לא נשמר ולא פורסם)")
        return

    # 4. commit, tag, push
    run(["git", "add"] + bumped)
    run(["git", "commit", "-m", f"Release v{ver}\n\n{notes}"])
    run(["git", "tag", "-a", f"v{ver}", "-m", f"v{ver}"])
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    run(["git", "push", "origin", f"{branch}:main"])
    run(["git", "push", "origin", f"v{ver}"])
    print(f"✔ v{ver} פורסמה. GitHub בונה את הקבצים: https://github.com/WallaBoohi/whatsapp-claude-bridge/actions")


BUMPED = ["VERSION", "dash/VERSION", ".claude-plugin/plugin.json", "agent/pyproject.toml", "agent/uv.lock", "CHANGELOG.md"]

if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        # a failed release must not leave the version bump behind: it blocked the next night ("uncommitted changes")
        if e.code not in (0, None) and "--dry-run" not in sys.argv and not str(e.code).startswith("✖ יש שינויים"):
            head = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=ROOT, capture_output=True, text=True).stdout
            if not head.startswith("Release v"):
                subprocess.run(["git", "checkout", "--"] + BUMPED, cwd=ROOT)
                print("↩️ מספר הגרסה הוחזר (השחרור לא בוצע)")
        raise
