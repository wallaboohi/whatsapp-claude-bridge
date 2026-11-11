"""Keeps the phone's adb link up (owner 11.11: "make sure someone brings adb back up when it drops").

The phone (Tailscale) listens on the fixed adb port 5555 only until it reboots; Android's "wireless debugging" then comes
back on a random port (30000-49999). heal():
  1. the fixed port answers                         -> ok
  2. reconnect the fixed port                       -> ok
  3. scan the phone for the wireless-debugging port, connect there (only a PC the phone already approved can), and turn
     the fixed port 5555 back on (adb tcpip 5555)   -> healed
  otherwise: unauthorized (the phone must approve this PC once) / no_port (wireless debugging is off on the phone) /
  unreachable (phone off or out of Tailscale).

Usage: uv run -q --no-project python -I -X utf8 tools/adb_keeper.py [status|heal]   (prints JSON)
The agent runs heal() every 5 minutes (agent.py adb_keeper, config "adb_keeper").
"""
import concurrent.futures as cf
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_sync  # noqa: E402  (adb_exe, DEVICE)

NO_WINDOW = phone_sync.NO_WINDOW
DEVICE = phone_sync.DEVICE
HOST, _, PORT = DEVICE.rpartition(":")
PORT = int(PORT or 5555)
STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "adb_keeper.json")


def _adb(*args, timeout=30):
    try:
        r = subprocess.run([phone_sync.adb_exe(), *args], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"


def _state(serial):
    """'device' / 'unauthorized' / 'offline' / '' from `adb devices`."""
    for line in _adb("devices")[1].splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == serial:
            return parts[1]
    return ""


def _answers(serial):
    rc, out = _adb("-s", serial, "shell", "echo ok", timeout=20)
    return rc == 0 and out.endswith("ok")


def _open(port, timeout=1.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((HOST, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _connect(serial):
    _adb("disconnect", serial, timeout=15)
    _adb("connect", serial, timeout=30)
    if _answers(serial):
        return "device"
    return _state(serial) or "failed"


def status():
    return {"device": DEVICE, "state": "ok" if _answers(DEVICE) else (_state(DEVICE) or "disconnected")}


def heal():
    if _answers(DEVICE):
        return {"state": "ok", "device": DEVICE}
    if _open(PORT, 3):
        st = _connect(DEVICE)
        if st == "device":
            return {"state": "ok", "device": DEVICE, "action": "reconnected"}
        if st == "unauthorized":
            return {"state": "unauthorized", "device": DEVICE}
    if not _open(PORT, 3) and not any(_open(p, 3) for p in (80, 443)) and not _ping():
        return {"state": "unreachable", "device": DEVICE}
    ports = _scan()
    unauthorized = False
    for p in ports:
        serial = f"{HOST}:{p}"
        st = _connect(serial)
        if st == "unauthorized":
            unauthorized = True
        if st != "device":
            _adb("disconnect", serial, timeout=15)
            continue
        _adb("-s", serial, "tcpip", str(PORT), timeout=30)
        time.sleep(5)
        _adb("disconnect", serial, timeout=15)
        try:
            json.dump({"port": p, "at": time.strftime("%Y-%m-%d %H:%M")}, open(STATE, "w", encoding="utf-8"))
        except OSError:
            pass
        if _connect(DEVICE) == "device":
            return {"state": "healed", "device": DEVICE, "via": p}
        return {"state": "healed_partial", "device": serial, "via": p}
    if unauthorized:
        return {"state": "unauthorized", "device": DEVICE}
    return {"state": "no_port" if _ping() else "unreachable", "device": DEVICE, "ports": ports}


def _scan():
    """Open ports in the wireless-debugging range, the last one found first. Scanned in 2,000-port chunks: one sweep of
    the whole range at once lost the open port (Tailscale drops the SYN flood; measured 11.11)."""
    try:
        last = json.load(open(STATE, encoding="utf-8")).get("port")
    except (OSError, ValueError):
        last = None
    if last and _open(last, 3):
        return [last]
    found = []
    with cf.ThreadPoolExecutor(400) as ex:
        for start in range(30000, 50000, 2000):
            found += [p for p in ex.map(lambda p: p if _open(p) else None, range(start, start + 2000)) if p]
            if found:
                break
            time.sleep(1)
    return found


def _ping():
    """The phone is on Tailscale and answers (tailscale ping works even when every TCP port is closed)."""
    try:
        r = subprocess.run(["tailscale", "ping", "--c", "1", "--timeout", "5s", HOST], capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    print(json.dumps(heal() if cmd == "heal" else status(), ensure_ascii=False))
