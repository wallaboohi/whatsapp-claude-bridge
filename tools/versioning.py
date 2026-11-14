"""The bridge's version-numbering LAW, in ONE place (owner 14.11 13:58). Nobody changes it: not an AI, not a command, not a flag.

THE LAW: every release gets the next number, and only that number. A release may hold many fixes and features.
  - The last part goes up by one: X.Y.1, X.Y.2 ... X.Y.10.
  - After X.Y.10 the next release is X.(Y+1).0, and then X.(Y+1).1 ... X.(Y+1).10 again (as the project did: 1.0.x -> 1.1.0).
  - Never skip, never step back, never reuse a number that already has a tag (even if its build failed).
  - The dashboard app always carries the tool's number.
There are no exceptions and no approvals: the next number is computed, never chosen.

  tools/release.py, tools/release_dash.py and the agent's nightly auto_release all take the number from here.
  uv run --no-project python tools/versioning.py next | current | check X.Y.Z
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


PATCH_MAX = 10  # owner 14.11: X.Y.1 ... X.Y.10, then X.(Y+1).0


def next_version():
    a, b, c = _t(current())
    return f"{a}.{b + 1}.0" if c >= PATCH_MAX else f"{a}.{b}.{c + 1}"


def approved():
    """No exceptions exist (owner 14.11): kept only so older callers do not break."""
    return None


def check(ver):
    """'' when `ver` may be released, otherwise the reason (Hebrew)."""
    if not VER_RE.match(ver or ""):
        return f"מספר גרסה לא תקין: {ver}"
    if ver in _tags():
        return f"הגרסה {ver} כבר קיבלה תג ופורסמה (או נכשלה בבנייה): אי אפשר להשתמש בה שוב. הגרסה הבאה היא {next_version()}"
    if _t(ver) <= _t(current()):
        return f"הגרסה {ver} לא גבוהה מהנוכחית ({current()}). הגרסה הבאה היא {next_version()}"
    if ver == next_version():
        return ""
    return (f"חוק הגרסאות של הגשר: אחרי {current()} באה {next_version()}, ורק היא (X.Y.1 עד X.Y.10, ואחריה X.Y+1.0). "
            f"{ver} נדחתה. לחוק אין חריגות.")


def check_dash(ver):
    """The dashboard app always carries the tool's number (no dash-only numbers: they made the 1.0.13 to 1.0.15 gap)."""
    tool = _read("VERSION")
    return "" if ver == tool else f"האפליקציה של הלוח חייבת לשאת את אותו מספר של הכלי ({tool}), לא {ver}"


def approve(ver):
    """No exceptions exist (owner 14.11): always refused."""
    return False


def consume(ver):
    """Nothing to consume: there are no approvals any more."""
    return None


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
    else:
        sys.exit("usage: versioning.py next | current | check X.Y.Z")
