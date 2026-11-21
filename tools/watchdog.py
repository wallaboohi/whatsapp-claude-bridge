"""Watchdog with automatic roll-back (owner 18.11 23:58): "if the bridge sees it does not come up, it returns itself to the
previous version after 20 minutes and tries again". Runs outside the agent (Task Scheduler, every 5 minutes), so a broken
agent cannot stop it. Per PC, nothing is sent anywhere; what it did is written to agent/watchdog_notice.json, which the
agent reports in the control group when it is up again.

  bridge: healthy = the bridge API answers, or (backup PC) the bridge waits at the lease gate (store/lease_status fresh).
          Down for >= 20 min after an exe swap (whatsapp-bridge-prev.exe exists, older than the running exe)
          -> the new exe is kept as whatsapp-bridge.exe.bad-<time>, the previous one goes back, the bridge is restarted.
  agent:  healthy = kv "heartbeat" in agent.db younger than 3 minutes (the scheduler writes it every 30 s).
          Remembers the last commit the agent was healthy on (agent/watchdog.json "good_head"). Down for >= 20 min while the
          code is on another commit -> the code files of the last good commit are restored (git checkout <good> -- agent tools
          whatsapp-mcp-server dash), agent/rollback.hold is written (the nightly save and rebuild do not commit or push the
          rolled-back files), and the agent process is restarted. One roll-back per bad commit; never touches git history.

  uv run -q --no-project python -I -X utf8 tools/watchdog.py [check|status]     (check = act; status = report only)
"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(ROOT, "agent")
BDIR = os.path.join(ROOT, "whatsapp-bridge")
STATE = os.path.join(AGENT, "watchdog.json")
NOTICE = os.path.join(AGENT, "watchdog_notice.json")
HOLD = os.path.join(AGENT, "rollback.hold")
LOG = os.path.join(AGENT, "watchdog.log")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
GRACE_MIN = 20
CODE_DIRS = ["agent", "tools", "whatsapp-mcp-server", "dash"]


def cfg():
    try:
        return json.load(open(os.path.join(AGENT, "config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def store_dir(c):
    s = c.get("store_dir") or "whatsapp-bridge/store"
    return s if os.path.isabs(s) else os.path.join(ROOT, s)


def log(*a):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] " + " ".join(str(x) for x in a)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def bridge_ok(c):
    st = store_dir(c)
    try:
        tok = open(os.path.join(st, "api_token"), encoding="utf-8").read().strip()
        req = urllib.request.Request((c.get("bridge_url") or "http://127.0.0.1:8765/api") + "/status",
                                     headers={"Authorization": "Bearer " + tok})
        urllib.request.urlopen(req, timeout=10).read()
        return True, "api"
    except Exception:
        pass
    try:  # a backup PC: the bridge waits at the lease gate on purpose and rewrites lease_status every minute
        s = open(os.path.join(st, "lease_status"), encoding="utf-8").read().split()
        if s and s[0] == "waiting" and time.time() - os.path.getmtime(os.path.join(st, "lease_status")) < 180:
            return True, "lease_wait"
    except OSError:
        pass
    return False, ""


def agent_ok():
    try:
        db = sqlite3.connect(f"file:{os.path.join(AGENT, 'agent.db')}?mode=ro", uri=True, timeout=10)
        hb = (db.execute("SELECT v FROM kv WHERE k = 'heartbeat'").fetchone() or [""])[0]
        db.close()
        return bool(hb) and (dt.datetime.now() - dt.datetime.fromisoformat(hb)).total_seconds() < 180
    except Exception:
        return False


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def notice(kind, text):
    n = load(NOTICE, [])
    n.append({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "text": text})
    save(NOTICE, n[-20:])


def kill(image):
    subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True, creationflags=NO_WINDOW)


def kill_agent():
    """Only the python process that runs agent.py (the run-agent loop starts it again)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -match 'agent\\.py' } "
          "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=NO_WINDOW, timeout=60)


def bridge_rollback(st):
    exe, prev = os.path.join(BDIR, "whatsapp-bridge.exe"), os.path.join(BDIR, "whatsapp-bridge-prev.exe")
    if not os.path.exists(prev) or not os.path.exists(exe) or os.path.getmtime(exe) <= os.path.getmtime(prev):
        return "no previous exe newer-than check passed; nothing to roll back to"
    if st.get("bridge_rolled") == os.path.getmtime(exe):
        return "already rolled back this exe once"
    bad = exe + ".bad-" + dt.datetime.now().strftime("%Y%m%d-%H%M")
    kill("whatsapp-bridge.exe")
    time.sleep(3)
    try:
        os.replace(exe, bad)
        os.replace(prev, exe)
    except OSError as e:
        return f"swap failed: {e}"
    st["bridge_rolled"] = os.path.getmtime(exe)
    notice("bridge", f"🛟 הגשר לא עלה {GRACE_MIN} דקות אחרי החלפת הגרסה, ולכן הוחזר לגרסה הקודמת. הגרסה שלא עלתה נשמרה בצד "
                     f"({os.path.basename(bad)}).")
    log("bridge rolled back; bad exe kept as", bad)
    return "rolled back"


def agent_rollback(st):
    good = st.get("good_head")
    rc, head = git("rev-parse", "HEAD")
    if rc or not good or head == good:
        return "no other good commit known"
    if st.get("agent_rolled_from") == head:
        return "already rolled back from this commit once"
    rc, out = git("checkout", good, "--", *CODE_DIRS)
    if rc:
        return "git checkout failed: " + out[-200:]
    with open(HOLD, "w", encoding="utf-8") as f:
        json.dump({"at": dt.datetime.now().isoformat(timespec="seconds"), "bad": head, "good": good}, f)
    st["agent_rolled_from"] = head
    notice("agent", f"🛟 הסוכן לא עלה {GRACE_MIN} דקות אחרי עדכון קוד ({head[:7]}), ולכן קבצי הקוד הוחזרו לגרסה האחרונה "
                    f"שעבדה ({good[:7]}). השינוי לא נשמר ולא נדחף עד שיתוקן.")
    log("agent code rolled back from", head, "to", good)
    kill_agent()
    return "rolled back"


RESTART_MIN = 10  # owner 21.11 20:45: down this long for any reason (not only after a code change) -> raised again
GOOD_TAG_MIN = 60  # a commit healthy this long gets a "good-<time>" tag: always a known place to go back to


def ensure_task(name):
    """The scheduled task that keeps a part running (its .cmd loops); started when it is not running."""
    ps = f"$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue; if ($t -and $t.State -ne 'Running') {{ Start-ScheduledTask -TaskName '{name}'; 'started' }}"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, creationflags=NO_WINDOW, timeout=60)
    return "started its task" if "started" in (r.stdout or "") else "task was running"


def raise_again(st, part, now):
    """Owner 21.11: the guard raises the agent / the bridge again when it is down, whatever the reason (a hang, a crash)."""
    key = f"{part}_raised_at"
    if now - st.get(key, 0) < RESTART_MIN * 60:
        return "raised a moment ago"
    st[key] = now
    if part == "agent":
        kill_agent()  # a hung agent; its loop (run-agent.cmd) starts it again
        how = ensure_task("WhatsApp agent")
        notice("agent", f"🛡️ השומר: הסוכן לא הגיב {RESTART_MIN} דקות, הופעל מחדש ({how}).")
    else:
        kill("whatsapp-bridge.exe")  # its loop (run-bridge.cmd) starts it again
        how = ensure_task("WhatsApp bridge")
        notice("bridge", f"🛡️ השומר: הגשר לא ענה {RESTART_MIN} דקות, הופעל מחדש ({how}).")
    log(part, "raised again:", how)
    return "raised again: " + how


def snapshot_work(st):
    """Owner 21.11 17:02: a hosted AI's `git reset --hard` wiped work that was not committed. Every check keeps a copy of what is
    not committed (tracked and new files, not ignored ones) as refs/guard/wip/<time> — never touching the files, the index or HEAD.
    Restore: git checkout <ref> -- <path>."""
    rc, dirty = git("status", "--porcelain")
    if rc or not dirty.strip():
        return "clean"
    import tempfile
    idx = os.path.join(tempfile.gettempdir(), "yb-guard-index")
    env = dict(os.environ, GIT_INDEX_FILE=idx)

    def g(*a):
        r = subprocess.run(["git", *a], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300, creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "").strip()
    g("read-tree", "HEAD")
    g("add", "-A", "--", ".", ":(exclude)outbox", ":(exclude)agent/outbox", ":(exclude)agent/tmp")
    rc, tree = g("write-tree")
    if rc or not tree or tree == st.get("wip_tree"):
        return "no change since the last copy"
    rc, commit = g("commit-tree", tree, "-p", "HEAD", "-m", f"guard: work not committed, {dt.datetime.now():%Y-%m-%d %H:%M}")
    if rc:
        return "copy failed"
    ref = f"refs/guard/wip/{dt.datetime.now():%Y%m%d-%H%M%S}"
    git("update-ref", ref, commit)
    st["wip_tree"] = tree
    log("work not committed kept as", ref)
    return ref


def tag_good(st, head, now):
    since = st.get("good_since") or {}
    if since.get("head") != head:
        st["good_since"] = {"head": head, "at": now}
        return ""
    if since.get("tagged") or now - since.get("at", now) < GOOD_TAG_MIN * 60:
        return ""
    tag = f"good-{dt.datetime.now():%Y%m%d-%H%M}"
    rc, out = git("tag", tag, head)
    since["tagged"] = tag if rc == 0 else ""
    st["good_since"] = since
    log("good version tagged", tag, head[:7])
    return tag


def check(act=True):
    c = cfg()
    st = load(STATE, {})
    now = time.time()
    out = {}
    bok, how = bridge_ok(c)
    if bok:
        st.pop("bridge_bad_since", None)
    else:
        st.setdefault("bridge_bad_since", now)
    out["bridge"] = how or f"down {int((now - st['bridge_bad_since']) / 60)} min"
    aok = agent_ok()
    if aok:
        st.pop("agent_bad_since", None)
        rc, head = git("rev-parse", "HEAD")
        if rc == 0 and not os.path.exists(HOLD):
            st["good_head"] = head
    else:
        st.setdefault("agent_bad_since", now)
    out["agent"] = "ok" if aok else f"down {int((now - st['agent_bad_since']) / 60)} min"
    if act and not bok and now - st["bridge_bad_since"] >= GRACE_MIN * 60:
        out["bridge_action"] = bridge_rollback(st)
        st["bridge_bad_since"] = now  # the next 20 minutes belong to the version that came back
    if act and not aok and now - st["agent_bad_since"] >= GRACE_MIN * 60:
        out["agent_action"] = agent_rollback(st)
        st["agent_bad_since"] = now
    if act and not aok and "agent_action" not in out and now - st["agent_bad_since"] >= RESTART_MIN * 60:
        out["agent_action"] = raise_again(st, "agent", now)
    if act and not bok and "bridge_action" not in out and now - st["bridge_bad_since"] >= RESTART_MIN * 60:
        out["bridge_action"] = raise_again(st, "bridge", now)
    if act:
        try:
            out["work_copy"] = snapshot_work(st)
        except Exception as e:
            out["work_copy"] = f"failed: {e}"
        if aok and bok:
            rc, head = git("rev-parse", "HEAD")
            if rc == 0:
                t = tag_good(st, head, now)
                if t:
                    out["good_tag"] = t
    st["checked"] = dt.datetime.now().isoformat(timespec="seconds")
    save(STATE, st)
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    print(json.dumps(check(act=cmd == "check"), ensure_ascii=False))
