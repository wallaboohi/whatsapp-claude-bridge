"""Keep the two computers in step (run from the desktop; the laptop is the ssh host 4-MINE).

  peer_sync.py status        show which local-only files differ and whether the peer's code/bridge is current
  peer_sync.py files         merge the local-only rule files in BOTH directions: a line one side lacks is appended to the
                             other side (never overwritten, a backup is kept on each side first);
                             JSON settings (config.json, agent/groups.json): only keys the other side lacks are added, secrets skipped
  peer_sync.py code          peer: git pull --ff-only from hub, rebuild the bridge through agent._rebuild_bridge() when Go
                             files changed, then show both versions

Merged files (one rule or entry per line): agent/home/bridge_rules.md, agent/home/dictionary.md.
Compared only, never merged (structured text): agent/home/AI-KNOWLEDGE.md.
"""
import datetime as dt
import hashlib
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEER = "4-MINE"
HOME = os.path.expanduser("~").replace("\\", "/")  # the peer uses the same profile layout
PEER_ROOT = f"{HOME}/whatsapp-mcp"
MERGE = ["agent/home/bridge_rules.md", "agent/home/dictionary.md"]
JSON_MERGE = ["agent/config.json", "agent/groups.json"]  # only keys the other side lacks; values never replaced
SECRET_WORDS = ("key", "token", "secret", "password", "pass")
COMPARE = ["agent/home/AI-KNOWLEDGE.md"]
SSH_NOISE = ("WARNING", "vulnerable", "upgraded", "post-quantum", "store now")


def ssh(cmd, timeout=600):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", PEER, cmd], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and not any(n in l for n in SSH_NOISE) and not l.startswith("**")]
    return "\n".join(lines)


def scp(src, dst):
    r = subprocess.run(["scp", "-q", src, dst], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode:
        raise RuntimeError("scp failed: " + r.stderr.strip()[-200:])


def fetch(rel, tmp):
    out = os.path.join(tmp, rel.replace("/", "_"))
    try:
        scp(f"{PEER}:{PEER_ROOT}/{rel}", out)
    except RuntimeError:
        return None
    return out


def read(path):
    try:
        return open(path, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    except OSError:
        return None


def lines_of(text):
    return [l for l in (text or "").split("\n") if l.strip()]


def _key(line):
    """A rule line "- *title (who, when):* text" is the same rule on both sides when the title matches (its text may have
    been edited on one side: that is reported as a conflict, never duplicated)."""
    t = line.strip()
    return t.split(":*", 1)[0] if t.startswith("- *") and ":*" in t else t


def merged(base, other):
    have = {_key(l) for l in lines_of(base)}
    return [l for l in lines_of(other) if l.strip() not in ("\\", "\\\\") and _key(l) not in have]


def conflicts(a, b):
    ours = {_key(l): l.strip() for l in lines_of(a)}
    return [k for k, v in ((_key(l), l.strip()) for l in lines_of(b)) if k in ours and ours[k] != v and k.startswith("- *")]


def missing_keys(base, other, path=""):
    """{dotted path: value} for keys in other that base lacks (nested dicts walked; secrets and existing values never touched)."""
    out = {}
    for k, v in other.items():
        if any(w in k.lower() for w in SECRET_WORDS):
            continue
        if k not in base:
            out[path + k] = v
        elif isinstance(v, dict) and isinstance(base[k], dict):
            out.update(missing_keys(base[k], v, path + k + "."))
    return out


def put(d, dotted, value):
    *parents, last = dotted.split(".")
    for p in parents:
        d = d.setdefault(p, {})
    d[last] = value


def merge_json(tmp):
    import json
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    for rel in JSON_MERGE:
        local = os.path.join(ROOT, rel)
        peer_copy = fetch(rel, tmp)
        try:
            mine, theirs = json.loads(read(local)), json.loads(read(peer_copy))
        except Exception:
            print(f"{rel}: skipped (missing or unreadable on one side)")
            continue
        if not (isinstance(mine, dict) and isinstance(theirs, dict)):
            print(f"{rel}: skipped (not an object)")
            continue
        to_me, to_peer = missing_keys(mine, theirs), missing_keys(theirs, mine)
        if to_me:
            os.replace(local, local + f".bak-{stamp}-peersync")
            for k, v in to_me.items():
                put(mine, k, v)
            open(local, "w", encoding="utf-8", newline="").write(json.dumps(mine, ensure_ascii=False, indent=2) + "\n")
        if to_peer:
            for k, v in to_peer.items():
                put(theirs, k, v)
            out = os.path.join(tmp, "peer_new_" + rel.replace("/", "_"))
            open(out, "w", encoding="utf-8", newline="").write(json.dumps(theirs, ensure_ascii=False, indent=2) + "\n")
            win = f"{PEER_ROOT}/{rel}".replace("/", "\\")
            ssh(f'copy /Y "{win}" "{win}.bak-{stamp}-peersync"')
            scp(out, f"{PEER}:{PEER_ROOT}/{rel}")
        print(f"{rel}: +{len(to_me)} keys here ({', '.join(to_me)[:120]}), +{len(to_peer)} on the peer (existing values untouched)")


def status(tmp):
    for rel in MERGE + COMPARE:
        mine, theirs = read(os.path.join(ROOT, rel)), read(fetch(rel, tmp) or "")
        if theirs is None:
            print(f"{rel}: missing on the peer")
        elif mine is None:
            print(f"{rel}: missing here")
        elif hashlib.sha1(mine.encode()).hexdigest() == hashlib.sha1(theirs.encode()).hexdigest():
            print(f"{rel}: identical")
        else:
            print(f"{rel}: DIFFERENT (here {len(lines_of(mine))} lines, peer {len(lines_of(theirs))}; "
                  f"{len(merged(theirs, mine))} only here, {len(merged(mine, theirs))} only on the peer, "
                  f"{len(conflicts(mine, theirs))} same rule edited differently)")
    print("code here:", subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
          "| peer:", ssh(f"git -C {PEER_ROOT} rev-parse --short HEAD"))


def merge_files(tmp):
    merge_json(tmp)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    for rel in MERGE:
        local = os.path.join(ROOT, rel)
        mine, peer_copy = read(local), fetch(rel, tmp)
        theirs = read(peer_copy) if peer_copy else None
        if mine is None and theirs is None:
            continue
        mine, theirs = mine or "", theirs or ""
        to_peer, to_me = merged(theirs, mine), merged(mine, theirs)
        if not to_peer and not to_me:
            print(f"{rel}: already in step")
            continue
        if to_me:
            os.replace(local, local + f".bak-{stamp}-peersync") if os.path.exists(local) else None
            open(local, "w", encoding="utf-8", newline="").write(mine.rstrip("\n") + "\n" + "\n".join(to_me) + "\n")
        if to_peer:
            out = os.path.join(tmp, "peer_new_" + rel.replace("/", "_"))
            open(out, "w", encoding="utf-8", newline="").write(theirs.rstrip("\n") + "\n" + "\n".join(to_peer) + "\n")
            win = f"{PEER_ROOT}/{rel}".replace("/", "\\")
            ssh(f'copy /Y "{win}" "{win}.bak-{stamp}-peersync"')
            scp(out, f"{PEER}:{PEER_ROOT}/{rel}")
        print(f"{rel}: +{len(to_me)} here, +{len(to_peer)} on the peer (backups *.bak-{stamp}-peersync)")


PEER_CODE_PS1 = r"""
$env:Path = 'C:\msys64\ucrt64\bin;' + $env:Path
Set-Location "$env:USERPROFILE\whatsapp-mcp"
$before = git rev-parse HEAD
git pull --ff-only hub main 2>&1 | Select-Object -Last 1
$after = git rev-parse HEAD
$go = git diff --name-only $before $after -- whatsapp-bridge | Where-Object { $_ -like '*.go' }
if ($go) {
  Set-Location agent
  $py = "import sys; sys.path.insert(0,'.'); import agent; print('rebuild:', repr(agent._rebuild_bridge()))"
  uv run --no-project python -I -X utf8 -c $py 2>&1 | Select-Object -Last 2
} else { 'no Go change: bridge left running' }
Get-Content "$env:USERPROFILE\whatsapp-mcp\VERSION"
"""


def update_code(tmp):
    ps1 = os.path.join(tmp, "peer_code.ps1")
    open(ps1, "w", encoding="utf-8").write(PEER_CODE_PS1)
    scp(ps1, f"{PEER}:{HOME}/peer_code_tmp.ps1")
    print(ssh(f"powershell -NoProfile -ExecutionPolicy Bypass -File {HOME}/peer_code_tmp.ps1", timeout=900))
    ssh(f"del {HOME}/peer_code_tmp.ps1".replace("/", "\\"))
    print("local VERSION:", open(os.path.join(ROOT, "VERSION")).read().strip())


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("status", "files", "code"):
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory(prefix="peersync_", dir=os.path.join(ROOT, "agent", "tmp")) as t:
        {"status": status, "files": merge_files, "code": update_code}[sys.argv[1]](t)
