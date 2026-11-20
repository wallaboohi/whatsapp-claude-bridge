"""Relay restart along the 4-PC chain (owner 20.11 12:01): every PC that can hold the bridge holds it once, the bridge is never
left with nobody, and every PC runs a tool check when it receives the bridge, after it restarts and before it passes it on.

    uv run -q --no-project python -I -X utf8 tools/chain_relay.py [--dry-run]     (on the desktop, only on the owner's order)

0. Preflight: every PC on hub/main, no tracked changes, no AI run in progress anywhere; the desktop holds the identity.
1. The backups restart on the new code one at a time in chain order (they stand by: no availability is lost); each must come
   back with a fresh agent (chain_update.update_peer) and pass a standby check.
2. The relay: desktop -> bedroom -> living room -> laptop -> desktop. The holder gets agent/handover.request (the agent runs
   /העבר גשר <next>); the identity moves in seconds with the messages received there; every conversation's next answer gets
   the recent messages as a context pack. The receiver must report "holding" and pass the holder check; the giver restarts
   on its own (release restart; the desktop waits "away" until the bridge comes back to it).
3. A step that fails stops the relay where the bridge is held now and says so (nothing is forced). Report to the control group.
"""
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain_update as cu  # noqa: E402
from chain_config import PCS  # noqa: E402

ROOT = cu.ROOT
ORDER = [p["name"] for p in PCS]  # נייח, חדר שינה, סלון, לפטופ
BY_NAME = {p["name"]: p for p in PCS}
LOG = os.path.join(ROOT, "agent", "chain_relay.log")

STATE = r"""
import json, os, sqlite3, subprocess
R = "C:/Users/walla/whatsapp-mcp"
out = {}
try:
    out["lease"] = open(R + "/whatsapp-bridge/store/lease_status", encoding="utf-8").read().strip()
except OSError:
    out["lease"] = ""
out["grant"] = os.path.exists(R + "/whatsapp-bridge/store/lease_grant")
out["handover"] = os.path.exists(R + "/whatsapp-bridge/store/handover.json")
out["policy"] = os.path.exists(R + "/whatsapp-bridge/store/send_policy.json")  # owner 20.11 23:45: draft storm without it
busy = []
try:
    db = sqlite3.connect("file:" + R + "/agent/agent.db?mode=ro", uri=True, timeout=10)
    for k, v in db.execute("SELECT k, v FROM kv WHERE k LIKE 'live_steps%'"):
        try:
            if not json.loads(v).get("done"):
                busy.append(k)
        except ValueError:
            pass
    hb = db.execute("SELECT v FROM kv WHERE k = 'heartbeat'").fetchone()
    out["heartbeat"] = hb[0] if hb else ""
    rh = db.execute("SELECT v FROM kv WHERE k = 'running_head'").fetchone()
    out["running_head"] = rh[0] if rh else ""
except sqlite3.Error as e:
    out["db_error"] = str(e)
out["busy"] = busy
g = lambda *a: subprocess.run(["git", "-C", R, *a], capture_output=True, text=True).stdout.strip()
out["head"] = g("rev-parse", "--short", "HEAD")
# owner 20.11 23:15 / 21.11 00:20: another Claude session's scratch worktree (.claude/worktrees/*) that the daily sync committed as a
# gitlink shows as " m" and stopped the nightly relay at 23:15 and the live round: it is not code of the bridge
out["dirty"] = [l for l in g("status", "--porcelain", "--untracked-files=no").splitlines() if l.strip() and ".claude/worktrees/" not in l]
print("@@J@@" + json.dumps(out, ensure_ascii=False))
"""

REQUEST = r"""
open("C:/Users/walla/whatsapp-mcp/agent/handover.request", "w", encoding="utf-8").write(C)
print("@@OK@@")
"""

SELFTEST = r"""
import subprocess
r = subprocess.run(["C:/Users/walla/whatsapp-mcp/agent/.venv/Scripts/python.exe", "-X", "utf8",
                    "C:/Users/walla/whatsapp-mcp/tools/selftest.py", "--quiet"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", cwd="C:/Users/walla/whatsapp-mcp", timeout=600)
print(r.stdout[-3000:])
print("@@RC@@" + str(r.returncode))
"""

ME = socket.gethostname().lower()


def log(*a):
    line = f"{dt.datetime.now():%H:%M:%S} " + " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_py(name, code, timeout=120):
    """The same Python snippet on the named PC (locally on the desktop)."""
    pc = BY_NAME[name]
    if pc["host"] == ME:
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    return cu.remote_py(pc["ssh"], code, timeout)


def state(name):
    rc, out = run_py(name, STATE, 90)
    for ln in out.splitlines():
        if ln.startswith("@@J@@"):
            return json.loads(ln[5:])
    return {"error": out[-300:] or f"rc {rc}"}


def request(name, target):
    rc, out = run_py(name, "C = " + json.dumps(target, ensure_ascii=False) + "\n" + REQUEST, 60)
    return "@@OK@@" in out


def tool_check(name, holder):
    """Holder: the full quiet self-test must pass. Standby: a fresh agent, waiting at the identity gate, on the hub commit."""
    st = state(name)
    if st.get("policy") is False:  # owner 20.11 23:45: 23:18 draft storm - a PC without store/send_policy.json must not hold or stand by
        return False, "חסר store/send_policy.json (הגשר היה הופך תשובות לטיוטות במספר האישי)"
    if holder:
        if not str(st.get("lease", "")).startswith("holding"):
            return False, f"לא מחזיק את ההזדהות ({st.get('lease') or st.get('error')})"
        rc, out = run_py(name, SELFTEST, 700)
        ok = "@@RC@@0" in out
        fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("✖")]
        return ok, "בדיקה עצמית עברה" if ok else "נכשל: " + "; ".join(fails[:4])[:300]
    if st.get("busy") is None or st.get("error"):
        return False, f"אין תשובה ({st.get('error')})"
    if not str(st.get("lease", "")).startswith("waiting"):
        return False, f"הגשר שם לא ממתין בשער ({st.get('lease')})"
    return True, f"ממתין בשער, קוד {st.get('head')}"


def wait_holding(name, minutes=5):
    end = time.time() + minutes * 60
    while time.time() < end:
        st = state(name)
        if str(st.get("lease", "")).startswith("holding"):
            return True
        time.sleep(3)
    return False


def holders():
    """The PCs whose bridge holds the identity right now (live, over ssh). Exactly one is correct."""
    return [n for n in ORDER if str(state(n).get("lease", "")).startswith("holding")]


def wait_stable(name, hub, seconds=60, timeout=420):
    """Owner 20.11 22:10: a PC earlier in the chain gets the bridge back only after ONE FULL MINUTE of continuous activity: its
    agent runs the hub code and its bridge waits at the gate, with no failed check in between (a failed check restarts the count)."""
    since, end = None, time.time() + timeout
    while time.time() < end:
        st = state(name)
        rh = str(st.get("running_head") or "")
        good = (not st.get("error") and str(st.get("lease", "")).startswith("waiting")
                and (not rh or hub.startswith(rh) or rh.startswith(hub)))
        if good:
            since = since or time.time()
            if time.time() - since >= seconds:
                return True, f"פעיל ברציפות {int(time.time() - since)} שנ'"
        else:
            since = None
        time.sleep(5)
    return False, f"לא היה פעיל ברציפות {seconds} שנ' תוך {timeout} שנ' ({st.get('lease') or st.get('error')})"


ABORT_HANDOVER = r"""
import os, time
d = "C:/Users/walla/whatsapp-mcp/whatsapp-bridge/store/handover.json"
if os.path.exists(d):
    os.replace(d, d + ".aborted-" + time.strftime("%Y%m%d-%H%M%S"))
print("@@OK@@")
"""


def recover_desktop(why):
    """Owner 21.11 00:30 (the bridge sat offline for ~5 minutes after a failed hop): when a hop fails and nobody holds the identity,
    the planned hand-over is cancelled on every PC (the file is moved aside, never deleted) and the desktop - the default holder -
    resumes and takes the bridge back. Returns True when the desktop holds again."""
    if holders():
        return False
    log("recover: nobody holds the identity after", why, "- cancelling the hand-over, the desktop returns")
    for n in ORDER[1:] + ORDER[:1]:  # the desktop last: its agent waits "away" while the file exists
        try:
            run_py(n, ABORT_HANDOVER, 60)
        except Exception as e:
            log("recover: could not reach", n, e)
    ok = wait_holding(ORDER[0], 3)
    log("recover: desktop holds again" if ok else "recover: the desktop did NOT take the bridge back")
    return ok


def plan_hops(auto):
    """Owner 20.11 22:10. Ordered restart (the owner's approval): the full live round desktop -> bedroom -> living room -> laptop ->
    desktop, so every planned restart validates the whole chain. Nightly (--auto): ONE backup (bedroom) holds while the desktop
    restarts, then the bridge returns; the other backups only get the new code while they stand by."""
    if auto:
        return [(ORDER[0], ORDER[1]), (ORDER[1], ORDER[0])]
    return list(zip(ORDER, ORDER[1:] + ORDER[:1]))


def report(text):
    path = os.path.join(ROOT, "agent", "tmp", "chain_relay_report.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)
    cfg = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
    subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "wa_send.py"), cfg["claude_group"], path],
                   capture_output=True, timeout=120)


def rebuild_desktop_bridge(since, hub):
    """While the desktop is away (its bridge at the gate, nobody depends on it): rebuild its bridge when Go code changed since the
    code it ran (agent._rebuild_bridge: the version stamp, swap, the run loop starts the new exe)."""
    go = [f for f in subprocess.run(["git", "-C", ROOT, "diff", "--name-only", since or hub, hub, "--", "whatsapp-bridge"],
                                    capture_output=True, text=True).stdout.split() if f.endswith(".go")]
    if not go:
        return "▫️ נייח: אין שינוי בקוד הגשר, אין בנייה"
    py = os.path.join(ROOT, "agent", ".venv", "Scripts", "python.exe")
    r = subprocess.run([py, "-I", "-X", "utf8", "-c", "import sys; sys.path.insert(0, '.'); import agent; print(repr(agent._rebuild_bridge()))"],
                       cwd=os.path.join(ROOT, "agent"), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200,
                       env=dict(os.environ, PATH=r"C:\msys64\ucrt64\bin;" + os.environ.get("PATH", "")))
    res = (r.stdout.strip().splitlines() or ["?"])[-1]
    ok = res in ("''", "")
    log("desktop bridge rebuild", ok, res[:200])
    return ("🔧 נייח: הגשר נבנה מחדש בזמן ההמתנה" if ok else "⚠️ נייח: בניית הגשר נכשלה: " + res[:150])


def detach_from_ssh(a):
    """Owner 20.11 13:00: a process started over ssh lives in the ssh session's job and dies with it - the relay was killed twice
    in the middle (12:51, 12:56). Started from an ssh session, the relay starts itself again through WMI (outside any job: its
    parent is the WMI service) and this copy returns at once. Returns the new process id, or 0 when it runs here."""
    if "--detached" in a or not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT")) or os.name != "nt":
        return 0
    import base64
    args = base64.b64encode(json.dumps(a + ["--detached"]).encode("utf-8")).decode()
    cmd = f'"{sys.executable}" -I -X utf8 "{os.path.abspath(__file__)}" --args-b64 {args}'
    q = lambda x: "'" + str(x).replace("'", "''") + "'"  # a PowerShell literal string: no escapes, '' is a quote
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=" + q(cmd)
          + "; CurrentDirectory=" + q(ROOT) + "}; Write-Output ($r.ReturnValue.ToString() + ' ' + $r.ProcessId)")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    parts = (r.stdout or "").split()
    if len(parts) >= 2 and parts[0] == "0":
        return int(parts[1])
    raise RuntimeError("could not detach the relay from the ssh session: " + ((r.stdout or "") + (r.stderr or ""))[-300:])


def ensure_holder_code(name, hub, minutes=6):
    """Owner 20.11 13:00: the holder hands over only with code that knows the relay. Its agent (not its bridge: WhatsApp stays
    connected) restarts on the hub code first when it still runs an older one. Returns (ok, why)."""
    st = state(name)
    running = str(st.get("running_head") or "")
    if running and (hub.startswith(running) or running.startswith(hub)):
        return True, f"כבר רץ על {hub}"
    if st.get("head") != hub:
        return False, f"הקוד בדיסק {st.get('head')} ולא {hub}"
    rc, out = run_py(name, 'open("C:/Users/walla/whatsapp-mcp/agent/restart.request", "w", encoding="utf-8").write('
                           '"סבב הפעלה מחדש: הסוכן של המחזיק עולה על הקוד החדש לפני המסירה (הגשר נשאר מחובר)")\nprint("@@OK@@")', 60)
    if "@@OK@@" not in out:
        return False, "לא הצלחתי לבקש הפעלה מחדש של הסוכן"
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(10)
        st = state(name)
        running = str(st.get("running_head") or "")
        if running and (hub.startswith(running) or running.startswith(hub)) and str(st.get("lease", "")).startswith("holding"):
            return True, f"הסוכן עלה על {hub}, הגשר נשאר מחובר"
    return False, f"הסוכן לא עלה על {hub} תוך {minutes} דקות"


def main(a):
    if "--args-b64" in a:  # the detached copy (detach_from_ssh)
        import base64
        a = json.loads(base64.b64decode(a[a.index("--args-b64") + 1]).decode("utf-8"))
    pid = detach_from_ssh(a)
    if pid:
        print(f"relay launched detached (pid {pid}); progress: agent/chain_relay.log", flush=True)
        return 0
    dry = "--dry-run" in a
    auto = "--auto" in a  # owner 20.11 12:25: a scheduled restart (nightly save / update) of the PC holding the bridge
    reason = a[a.index("--reason") + 1] if "--reason" in a and a.index("--reason") + 1 < len(a) else ""
    lines = []
    subprocess.run(["git", "-C", ROOT, "fetch", "-q", "hub"], capture_output=True, timeout=120)
    hub = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "hub/main"], capture_output=True, text=True).stdout.strip()
    log("relay start", "dry" if dry else "LIVE", "auto" if auto else "ordered", "hub", hub, reason)
    # 0. preflight (scheduled: a run in progress is waited for, up to 30 minutes)
    end = time.time() + (30 * 60 if auto else 0)
    while True:
        states = {n: state(n) for n in ORDER}
        if not any(st.get("busy") for st in states.values()) or time.time() >= end:
            break
        time.sleep(60)
    bad = []
    for n, st in states.items():
        if st.get("error"):
            bad.append(f"{n}: לא עונה")
        elif n == ORDER[0] and st.get("head") != hub:
            bad.append(f"{n}: קוד {st.get('head')} ולא {hub}")  # the backups are pulled in step 1
        elif st.get("dirty"):
            bad.append(f"{n}: שינויים לא שמורים")
        elif st.get("busy"):
            bad.append(f"{n}: ריצה פעילה ({', '.join(st['busy'])})")
    try:  # owner 20.11 12:17: the list of fixes in the pipe - nothing still being worked on goes into a restart
        items = json.load(open(os.path.join(ROOT, "agent", "pending.json"), encoding="utf-8")).get("items") or []
    except (OSError, ValueError):
        items = []
    for it in items:
        if it.get("status") == "בעבודה":
            bad.append(f"ברשימת התיקונים בקנה #{it['id']} עדיין בעבודה ({it.get('by') or '?'}): {it.get('what', '')[:60]}")
    if not str(states[ORDER[0]].get("lease", "")).startswith("holding"):
        bad.append("הנייח לא מחזיק את ההזדהות עכשיו")
    for n, st in states.items():
        log("preflight", n, json.dumps({k: st.get(k) for k in ("head", "lease", "busy", "dirty")}, ensure_ascii=False))
    if bad:
        log("preflight failed:", "; ".join(bad))
        if not dry:
            report("⛔ *סבב ההפעלה מחדש לא התחיל:*\n" + "\n".join("• " + b for b in bad))
        return 1
    if dry:
        log("dry run: preflight ok; would restart", ", ".join(ORDER[1:]), "then relay", " -> ".join(ORDER + ORDER[:1]))
        return 0
    # 1. backups on the new code, one at a time (standby: no availability lost)
    for n in ORDER[1:]:
        r = cu.update_peer(BY_NAME[n], False)
        ok, why = tool_check(n, False) if r.get("ok") else (False, r.get("why"))
        log("restart", n, ok, why)
        lines.append(f"{'✅' if ok else '❌'} {n}: הופעל מחדש · {why}")
        if not ok:
            report("⛔ *סבב ההפעלה מחדש נעצר* (הגשר נשאר בנייח):\n" + "\n".join(lines))
            return 1
    # 1b. the holder's agent on the new code first (owner 20.11 13:00: at 12:51 it still ran 10:59 code that did not know
    #     handover.request, so the relay could never have moved the bridge)
    ok, why = ensure_holder_code(ORDER[0], hub)
    log("holder code", ORDER[0], ok, why)
    lines.append(f"{'✅' if ok else '❌'} {ORDER[0]}: {why}")
    if not ok:
        report("⛔ *סבב ההפעלה מחדש נעצר* לפני המסירה, הגשר ב" + ORDER[0] + ":\n" + "\n".join(lines))
        return 1
    # 2. the relay
    hops = plan_hops(auto)
    for giver, taker in hops:
        if taker == ORDER[0]:  # the desktop is back on the new code: a full minute of activity before it gets the bridge
            ok_s, why_s = wait_stable(ORDER[0], hub)
            log("stable", ORDER[0], ok_s, why_s)
            lines.append(f"{'✅' if ok_s else '❌'} {ORDER[0]}: {why_s}")
            if not ok_s:
                report("⛔ *סבב ההפעלה מחדש נעצר*, הגשר נשאר ב" + giver + ":\n" + "\n".join(lines) + "\nלחזרה: */העבר גשר נייח*")
                return 1
        t0 = time.time()
        if not request(giver, taker if taker != ORDER[0] else "נייח"):
            lines.append(f"❌ {giver} ← {taker}: לא הצלחתי לכתוב את בקשת המסירה")
            report("⛔ *סבב ההפעלה מחדש נעצר*, הגשר ב" + giver + ":\n" + "\n".join(lines))
            return 1
        held = wait_holding(taker, 6 if taker == ORDER[0] else 4)
        secs = int(time.time() - t0)
        if not held:
            lines.append(f"❌ {giver} ← {taker}: לא קיבל את הגשר תוך {secs} שנ'")
            back = recover_desktop(f"{giver} -> {taker}")
            lines.append("↩️ הנייח לקח את הגשר חזרה" if back else "⚠️ הגשר לא חזר לנייח אוטומטית: בדוק /מצב")
            report("⛔ *סבב ההפעלה מחדש נעצר.* " + "\n".join(lines))
            return 1
        ok, why = tool_check(taker, True)
        now_holding = holders()  # live: exactly one connection on the identity after every hop
        if now_holding != [taker]:
            ok, why = False, f"חיבור לא תקין: מחזיקים עכשיו {now_holding or 'אף אחד'}"
        log("hop", giver, "->", taker, secs, "s", ok, why)
        lines.append(f"{'✅' if ok else '⚠️'} {giver} → {taker}: מחזיק אחרי {secs} שנ' · {why}")
        if not ok and (taker != ORDER[0] or len(holders()) != 1):
            report("⛔ *סבב ההפעלה מחדש נעצר*, הגשר ב" + taker + " (מחזיק אבל ניסוי הכלים נכשל):\n" + "\n".join(lines)
                   + "\nלחזרה: */העבר גשר נייח*")
            return 1
        if giver == ORDER[0]:
            lines.append(rebuild_desktop_bridge(states[ORDER[0]].get("running_head") or "", hub))
    subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "pending.py"), "live"], capture_output=True, timeout=60)
    report("🔁 *סבב הפעלה מחדש עם מסירת הגשר הושלם* (קוד " + hub + (f", {reason}" if reason else "") + "):\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
