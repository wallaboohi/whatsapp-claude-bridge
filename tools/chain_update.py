"""Rolling update along the 4-PC chain (owner 18.11 23:16 / 23:58): "restart the bridge on an update and push fixes as a chain".

Order: the backups first, one at a time, in chain order (bedroom -> living room -> laptop), each verified before the next;
the PC that holds the bridge (the desktop) last. A backup in standby holds no WhatsApp session, so restarting it costs
nothing; the holder restarts once, at the end (the Go bridge keeps WhatsApp connected while only the agent restarts).
A PC that does not come back stops the chain (the others keep the old, working version) and is reported; its own
watchdog (tools/watchdog.py) rolls it back after 20 minutes.

    uv run -q --no-project python -I -X utf8 tools/chain_update.py [--dry-run] [--only PERROOM,SALON] [--no-self]

Per backup: git pull --ff-only hub main (stops on anything but a fast-forward), the bridge is rebuilt through
agent._rebuild_bridge() only when Go files changed, agent/restart.request (reason in Hebrew), then wait until its agent
writes a fresh heartbeat on the new commit. This PC: pull, then restart.request (the agent restarts after the current
answer). Prints one JSON line per PC.
"""
import tempfile
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chain_config import PCS  # noqa: E402

NOISE = ("WARNING", "vulnerable", "upgraded", "post-quantum", "store now")
HOME = "C:/Users/walla"  # the bridge lives in C:/Users/walla/whatsapp-mcp on every PC (the ssh user differs)
REMOTE = r"""
$ErrorActionPreference = 'Continue'
$env:Path = 'C:\msys64\ucrt64\bin;' + $env:Path
Set-Location "C:\Users\walla\whatsapp-mcp"
# over ssh Windows will not pass uv's junction (cpython-3.11-...): "untrusted mount point" (os error 448). Use the real folder.
# PowerShell variables are case-insensitive: $PY and $py would be the SAME variable. Owner 19.11: that
# collision silently ran the python code as a command, so a Go change was reported as rebuilt without being built.
$pyexe = "C:\Users\walla\whatsapp-mcp\agent\.venv\Scripts\python.exe"
if (-not (Test-Path $pyexe)) {
  $d = Get-ChildItem "$env:APPDATA\uv\python" -Directory -EA 0 | Where-Object Name -match '^cpython-3[.][0-9]+[.][0-9]+-' | Sort-Object Name | Select-Object -Last 1
  if ($d) { $pyexe = $d.FullName + '\python.exe' } else { $pyexe = (Get-Command python -EA SilentlyContinue).Source }
}
$before = git rev-parse HEAD
if ($env:DRY) { git fetch -q hub 2>&1 | Out-Null; $pull = 'dry-run: fetched only' } else { $pull = git pull --ff-only hub main 2>&1 | Select-Object -Last 1 }
$after = git rev-parse HEAD
$merged = $false
if (-not $env:DRY -and $after -ne (git rev-parse hub/main)) {
  # owner 21.11 00:10: the PCs commit their own "Daily sync" data commits, which never reached hub, so a plain fast-forward failed on
  # three PCs and stopped every rolling update. Merge hub into the local branch (hub wins only on conflicting hunks: backup copies of
  # config files) and push the result, so hub holds every PC's data too. A real conflict or a rejected push stops the chain as before.
  $dirty = git status --porcelain --untracked-files=no | Where-Object { $_ -notmatch '[.]claude/worktrees/' }
  if (-not $dirty) {
    git merge hub/main -X theirs --no-edit 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { git merge --abort 2>&1 | Out-Null } else { git push hub HEAD:main 2>&1 | Out-Null; git fetch -q hub 2>&1 | Out-Null; $merged = $true }
    $after = git rev-parse HEAD
  }
}
$hub = git rev-parse hub/main
$go = git diff --name-only $before $after -- whatsapp-bridge | Where-Object { $_ -like '*.go' }
$rb = ''
$rbok = $true
if ($go -and -not $env:DRY) {
  Push-Location agent
  $pycode = "import sys; sys.path.insert(0,'.'); import agent; print(repr(agent._rebuild_bridge()))"
  $rb = (& $pyexe -I -X utf8 -c $pycode 2>&1 | Select-Object -Last 1) -join ' '
  $rbok = ($rb -eq "''" -or $rb -eq '')
  Pop-Location
}
# owner 21.11 00:22: the laptop already had the new code on disk (merged by hand) but its agent still ran the old one: nothing was
# pulled, so no restart was asked. Compare the commit the running agent started on with the checkout.
$runcode = "import sqlite3;db=sqlite3.connect('file:C:/Users/walla/whatsapp-mcp/agent/agent.db?mode=ro',uri=True,timeout=10);r=db.execute('select v from kv where k=?',('running_head',)).fetchone();print(r[0] if r else '')"
$running = (& $pyexe -I -X utf8 -c $runcode 2>$null | Select-Object -Last 1)
$stale = [bool]$running -and (-not $after.StartsWith($running.Substring(0, [Math]::Min(7, $running.Length))))
if (($after -ne $before -or $stale) -and -not $env:DRY) {
  [IO.File]::WriteAllText("C:\Users\walla\whatsapp-mcp\agent\restart.request", "עדכון מדורג בשרשרת: $($after.Substring(0,7))", [Text.UTF8Encoding]::new($false))
}
@{before=$before; after=$after; hub=$hub; pull="$pull"; go=[bool]$go; rebuild=$rb; rebuild_ok=$rbok; restart=($after -ne $before -or $stale); merged=$merged; running="$running" } | ConvertTo-Json -Compress
"""
HB = r"""
import os, subprocess
req = os.path.exists("C:/Users/walla/whatsapp-mcp/agent/restart.request")
ps = '''Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'agent[.]py' } | ForEach-Object { $_.CreationDate.ToString('s') } | Sort-Object | Select-Object -First 1'''
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
print(("pending " if req else "") + (r.stdout.strip() or "none"))
"""


def ssh(host, cmd, timeout=900):
    # output goes to a temp file, not a pipe: when this tool runs inside an ssh session (e.g. on the desktop, started from
    # the laptop), Windows OpenSSH with piped stdout never exits, even for "echo" (found 19.11: 60 s hang to the bedroom PC)
    with tempfile.TemporaryFile() as f:
        rc = subprocess.run(["ssh", "-n", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", host, cmd],
                            stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        f.seek(0)
        out = f.read().decode("utf-8", "replace")
    return rc, "\n".join(l for l in out.splitlines() if l.strip() and not any(n in l for n in NOISE))


def scp(src, dst):
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", src, dst], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def remote_py(host, code, timeout=60):
    tmp = os.path.join(ROOT, "agent", "tmp", f"chain_{os.getpid()}.py")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    open(tmp, "w", encoding="utf-8").write(code)
    scp(tmp, f"{host}:{HOME}/chain_tmp.py")
    os.remove(tmp)
    # not "uv run": in an ssh logon uv's python junction gives os error 448, so run the real versioned folder
    # (the laptop has no uv-managed python: fall back to "uv python find", which is a plain path there)
    return ssh(host, 'powershell -NoProfile -Command "$d = Get-ChildItem $env:APPDATA\\uv\\python -Directory -EA 0 | '
                     "Where-Object Name -match '^cpython-3[.]11[.][0-9]+-' | Sort-Object Name | Select-Object -Last 1; "
                     "if ($d) { $x = $d.FullName + '\\python.exe' } else { $x = (uv python find 3.11) }; "
                     f"& $x -I -X utf8 {HOME}/chain_tmp.py\"", timeout)


def heartbeat(host):
    rc, out = remote_py(host, HB)
    return out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""


def update_peer(pc, dry):
    host = pc["ssh"]
    tmp = os.path.join(ROOT, "agent", "tmp", f"chain_{os.getpid()}.ps1")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    open(tmp, "w", encoding="utf-8-sig").write(REMOTE)
    scp(tmp, f"{host}:{HOME}/chain_tmp.ps1")
    os.remove(tmp)
    hb0 = heartbeat(host)
    rc, out = ssh(host, ("set DRY=1&& " if dry else "") + f"powershell -NoProfile -ExecutionPolicy Bypass -File {HOME}/chain_tmp.ps1")
    try:
        res = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"pc": pc["name"], "ok": False, "why": "no answer: " + out[-200:]}
    res = {"pc": pc["name"], **res}
    if dry:
        return {**res, "ok": True, "behind": res.get("after") != res.get("hub"), "restart": False}
    if res.get("after") != res.get("hub"):
        return {**res, "ok": False, "why": "not on hub/main after pull (local commits or not fast-forward) - stopped"}
    if res.get("go") and res.get("rebuild_ok") is False:
        return {**res, "ok": False, "why": "bridge rebuild failed on this PC - chain stopped"}
    if dry or not res.get("restart"):
        return {**res, "ok": True}
    end = time.time() + 6 * 60
    while time.time() < end:  # the agent restarts within ~1-2 min (it finishes its current answer first)
        time.sleep(20)
        hb = heartbeat(host)  # "<agent start time>", or "pending ..." while restart.request still waits
        if hb and not hb.startswith("pending") and hb != "none" and hb > hb0:
            return {**res, "ok": True, "agent_started": hb}
    return {**res, "ok": False, "why": "agent did not write a fresh heartbeat within 6 minutes - chain stopped"}


start_wait = time.time()


def main(a):
    global start_wait
    dry = "--dry-run" in a
    only = a[a.index("--only") + 1].lower().split(",") if "--only" in a else None
    me = socket.gethostname().lower()
    order = [p for p in PCS[1:] + PCS[:1]]  # backups in chain order, then the host
    results = []
    for pc in order:
        if pc["host"] == me or (me in ("lap-yakir", "4-mine") and pc["host"] == "lap-yakir"):
            continue
        if only and pc["ssh"].lower() not in only and pc["host"] not in only:
            continue
        if pc["host"] == "yb-pc-hw":
            continue  # the host is updated last, by itself (run this tool on it, or its nightly save)
        start_wait = time.time()
        r = update_peer(pc, dry)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        results.append(r)
        if not r.get("ok"):
            return 1
    if "--no-self" not in a:
        r = subprocess.run(["git", "pull", "--ff-only", "hub", "main"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(json.dumps({"pc": me, "ok": False, "why": "pull not fast-forward: " + (r.stdout + r.stderr)[-200:]}, ensure_ascii=False))
            return 1
        if not dry:
            head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
            with open(os.path.join(ROOT, "agent", "restart.request"), "w", encoding="utf-8") as f:
                f.write(f"עדכון מדורג בשרשרת: {head} (המחשב הזה אחרון)")
        print(json.dumps({"pc": me, "ok": True, "restart": not dry}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
