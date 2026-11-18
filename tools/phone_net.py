"""Phone network on demand (owner 18.11 23:16 / 23:58): the bridge turns the phone's Tailscale on when it needs the phone,
over ntfy (plain internet push, works while Tailscale is off). Simple and reliable on purpose: one private ntfy topic, a
listener in Termux on the phone (~/bin/bridge-phone.sh, started by Termux:Boot) that broadcasts Tailscale's own
CONNECT_VPN / DISCONNECT_VPN intents, and an answer on "<topic>-ack".

    uv run -q --no-project python -I -X utf8 tools/phone_net.py status        # Tailscale reachable? listener answers?
    uv run -q --no-project python -I -X utf8 tools/phone_net.py up [--wait 60] # Tailscale on, wait until the phone answers
    uv run -q --no-project python -I -X utf8 tools/phone_net.py down          # Tailscale off (only on the owner's request)
    uv run -q --no-project python -I -X utf8 tools/phone_net.py ping          # the listener answers over ntfy (no Tailscale)
    uv run -q --no-project python -I -X utf8 tools/phone_net.py idle <min>    # phone turns Tailscale off after <min> idle (0 = never)

The topic is a secret: secrets/phone_ntfy.topic (copied to every PC, not in git). Prints JSON.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPIC_FILE = os.path.join(ROOT, "secrets", "phone_ntfy.topic")
SERVER = "https://ntfy.sh"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PHONE = "poco-yakir"


def topic():
    try:
        return open(TOPIC_FILE, encoding="utf-8").read().strip()
    except OSError:
        return ""


def _post(msg, suffix=""):
    t = topic()
    if not t:
        raise RuntimeError("no topic: secrets/phone_ntfy.topic is missing")
    req = urllib.request.Request(f"{SERVER}/{t}{suffix}", data=msg.encode(), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode() or "{}").get("time") or int(time.time())


def _acks(since):
    """Answers of the listener posted at or after the unix time `since`."""
    t = topic()
    try:
        with urllib.request.urlopen(f"{SERVER}/{t}-ack/json?poll=1&since={int(since) - 1}", timeout=15) as r:
            out = []
            for line in r.read().decode().splitlines():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("event") == "message":
                    out.append(m.get("message") or "")
            return out
    except OSError:
        return []


def ts_reachable(timeout_s=5):
    """The phone answers on Tailscale (tailscale ping works even when every TCP port is closed)."""
    try:
        r = subprocess.run(["tailscale", "ping", "--c", "1", "--timeout", f"{timeout_s}s", PHONE], capture_output=True,
                           timeout=timeout_s + 15, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def send(cmd, wait_ack=20):
    """Posts a command and waits for the listener's answer; returns the answer text or ''."""
    at = _post(cmd)
    end = time.time() + wait_ack
    while time.time() < end:
        time.sleep(2)
        for a in _acks(at):
            if a.split()[0].startswith(cmd.split()[0][:2]) or (cmd == "ping" and a.startswith("pong")):
                return a
    return ""


def up(wait=60):
    """Tailscale on (unless it already answers), then wait until the phone answers on Tailscale."""
    if ts_reachable(3):
        try:
            _post("up")  # renew the idle timer on the phone; no wait
        except Exception:
            pass
        return {"state": "ok", "action": "already_up"}
    ack = send("up", 20)
    end = time.time() + wait
    while time.time() < end:
        if ts_reachable(4):
            return {"state": "ok", "action": "turned_on", "ack": ack}
        time.sleep(3)
    return {"state": "no_answer", "ack": ack or "", "hint": "listener answered but Tailscale did not come up" if ack
            else "the phone's listener did not answer (phone off / no internet / Termux killed)"}


def main(a):
    cmd = a[0] if a else "status"
    if cmd == "status":
        out = {"tailscale": ts_reachable(4), "topic": bool(topic())}
        out["listener"] = bool(send("ping", 15)) if out["topic"] else False
    elif cmd == "up":
        w = int(a[a.index("--wait") + 1]) if "--wait" in a else 60
        out = up(w)
    elif cmd == "down":
        out = {"state": "ok" if send("down", 20) else "no_answer"}
    elif cmd == "ping":
        out = {"answer": send("ping", 20)}
    elif cmd == "idle" and len(a) > 1 and a[1].isdigit():
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "phone", f"echo {int(a[1])} > ~/.bridge_idle_min"],
                           capture_output=True, timeout=40, creationflags=NO_WINDOW)
        out = {"state": "ok" if r.returncode == 0 else "failed", "idle_min": int(a[1])}
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
