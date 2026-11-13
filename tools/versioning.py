"""The bridge's version-numbering rules, in ONE place (owner 14.11).

The rule (as the project started it): one number per release, one patch at a time (1.1.1 -> 1.1.2 -> 1.1.3 ...). Never skip a number, never
step back, never reuse a number that already has a tag, and the dashboard app carries the same number as the tool. A different number needs
the owner's explicit approval, given through the bridge (`/גרסה אשר X.Y.Z` in WhatsApp) — an AI cannot change how the bridge numbers itself.

  tools/release.py and tools/release_dash.py ask this module before they touch any version file.
  uv run --no-project python tools/versioning.py next | current | check X.Y.Z | approve X.Y.Z
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVAL = os.path.join(ROOT, "agent", "version_approval.json")
VER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _t(v):
    return tuple(int(x) for x in v.split("."))


def _read(rel):
    try:
        v = open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read().strip().split()[0]
        return v if VER_RE.match(v) else None
    except (OSError, IndexError):
        return None


def _tags():
    """Versions that already have a git tag (vX.Y.Z), local ones and the remotes' when they answer."""
    out = set()
    for cmd in (["git", "tag", "--list", "v*"], ["git", "ls-remote", "--tags", "origin"]):
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=25)
        except Exception:
            continue
        for m in re.finditer(r"v(\d+\.\d+\.\d+)(?:\^\{\})?(?:\s|$)", r.stdout or ""):
            out.add(m.group(1))
    return out


def known():
    """Every version number the project has seen: the tool, the dashboard app and every tag."""
    return sorted({v for v in (_read("VERSION"), _read("dash/VERSION")) if v} | _tags(), key=_t)


def current():
    return known()[-1] if known() else "1.0.0"


def next_version():
    a, b, c = _t(current())
    return f"{a}.{b}.{c + 1}"


def approved():
    try:
        return json.load(open(APPROVAL, encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def check(ver):
    """'' when `ver` may be released, otherwise the reason (Hebrew)."""
    if not VER_RE.match(ver or ""):
        return f"מספר גרסה לא תקין: {ver}"
    if ver in _tags():
        return f"הגרסה {ver} כבר קיבלה תג ופורסמה (או נכשלה בבנייה): אי אפשר להשתמש בה שוב. הגרסה הבאה היא {next_version()}"
    if _t(ver) <= _t(current()):
        return f"הגרסה {ver} לא גבוהה מהנוכחית ({current()}). הגרסה הבאה היא {next_version()}"
    if ver == next_version() or ver == approved():
        return ""
    return (f"חוק הגשר: תיקון אחד בכל שחרור. אחרי {current()} באה {next_version()}, לא {ver}. "
            f"חריגה דורשת אישור של יקיר בווצאפ: /גרסה אשר {ver}")


def check_dash(ver):
    """The dashboard app always carries the tool's number (no dash-only numbers: they made the 1.0.13 to 1.0.15 gap)."""
    tool = _read("VERSION")
    return "" if ver == tool else f"האפליקציה של הלוח חייבת לשאת את אותו מספר של הכלי ({tool}), לא {ver}"


def approve(ver):
    if not VER_RE.match(ver or "") or _t(ver) <= _t(current()):
        return False
    json.dump({"version": ver, "at": dt.datetime.now().isoformat(timespec="seconds"), "by": "owner (/גרסה אשר)"}, open(APPROVAL, "w", encoding="utf-8"))
    return True


def consume(ver):
    """After a release: an approval is good for one release only."""
    if approved() == ver:
        try:
            os.remove(APPROVAL)
        except OSError:
            pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "next"
    if cmd == "next":
        print(next_version())
    elif cmd == "current":
        print(current())
    elif cmd == "check":
        why = check(sys.argv[2])
        print(why or "ok")
        sys.exit(1 if why else 0)
    elif cmd == "approve":
        sys.exit(0 if approve(sys.argv[2]) else 1)
    else:
        sys.exit("usage: versioning.py next | current | check X.Y.Z | approve X.Y.Z")
