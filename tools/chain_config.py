"""Owner 18.11.2026 11:33: the 4-PC backup chain, in one place.

    desktop (YB-PC-HW, host) -> bedroom (PERROOM) -> living room (SALON) -> laptop (lap-yakir)

One WhatsApp identity (the desktop's linked device) moves along the chain ("session_share"); nobody links a device of
its own. This script merges the chain fields into THIS PC's agent/config.json (other fields are kept; a backup copy is
written first): peers, failover.defer_to, failover.self_name, session_share.

    uv run --no-project python -X utf8 tools/chain_config.py [--dry-run]
"""
import json
import os
import shutil
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "agent", "config.json")

# name, ssh alias, dashboard port (each PC reaches a peer's dashboard on 127.0.0.1:<port of that peer>)
PCS = [
    {"host": "yb-pc-hw", "name": "נייח", "ssh": "ybhw", "port": 8781},
    {"host": "perroom", "name": "חדר שינה", "ssh": "PERROOM", "port": 8784, "ollama_port": 11437},
    {"host": "salon-pc", "name": "סלון", "ssh": "SALON", "port": 8783, "ollama_port": 11436},
    {"host": "lap-yakir", "name": "לפטופ", "ssh": "4-MINE", "port": 8782},
]
# minutes between fresh copies of the identity (first backup most often). Owner 18.11 19:42: a take-over installs the last
# copy, so a shorter gap means a fresher session (the copy travels compressed now: about a third of the bytes)
SESSION = {"חדר שינה": 1, "סלון": 2, "לפטופ": 3}


def chain_fields(me):
    """The chain part of the config for the PC whose hostname is me."""
    i = next(n for n, p in enumerate(PCS) if p["host"] == me.lower())
    pc = PCS[i]
    out = {"session_share": {"enabled": True, **({"pull_minutes": SESSION[pc["name"]]} if pc["name"] in SESSION else {})}}
    peers = []
    for p in PCS:
        if p is pc:
            continue
        e = {"name": p["name"], "url": f"http://127.0.0.1:{p['port']}"}
        if i == 0 and p["name"] == "לפטופ":
            e["ssh"] = p["ssh"]  # the laptop's own tunnel forwards its dashboard to the desktop's 8781 (-R): no second tunnel
            e["url"] = "http://127.0.0.1:8781"
        else:
            e["tunnel"] = p["ssh"]
            if p.get("ollama_port"):
                e["ollama_port"] = p["ollama_port"]  # that PC's Ollama reachable here on 127.0.0.1:<port> (owner 18.11 13:06)
        if pc["name"] == "לפטופ" and p["name"] == "נייח":
            e["reverse"] = True  # keeps -R 8781 (the desktop reaches the laptop) and the Ollama pair
        peers.append(e)
    out["peers"] = peers
    if i:
        # owner 18.11 19:42: every hand-over within 7 seconds -> automatic and immediate (the agent's chain_watch decides in
        # seconds; after_minutes 0 also for the old minute-based path)
        out["failover"] = {"self_name": pc["name"], "defer_to": [p["name"] for p in PCS[1:i]], "auto": True, "after_minutes": 0}
    return out


def merge(cfg, fields):
    cfg = dict(cfg)
    known = {p.get("name"): p for p in cfg.get("peers") or []}
    cfg["peers"] = [{**known.get(p["name"], {}), **p} for p in fields["peers"]]
    for p in cfg["peers"]:
        if "tunnel" in p and "ssh" in p and p["name"] != "לפטופ":
            p.pop("ssh")
    cfg["session_share"] = {**(cfg.get("session_share") or {}), **fields["session_share"]}
    if "failover" in fields:
        if not cfg.get("failover"):
            raise SystemExit("no failover section on this backup PC: copy it from another backup first")
        cfg["failover"] = {**cfg["failover"], **fields["failover"]}
    return cfg


if __name__ == "__main__":
    me = socket.gethostname()
    cfg = json.load(open(CFG, encoding="utf-8"))
    new = merge(cfg, chain_fields(me))
    print(json.dumps({k: new.get(k) for k in ("peers", "session_share")}, ensure_ascii=False, indent=1))
    print("defer_to:", (new.get("failover") or {}).get("defer_to"))
    if "--dry-run" not in sys.argv:
        shutil.copy2(CFG, CFG + ".pre-chain-" + time.strftime("%Y%m%d-%H%M%S"))
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        print("written", CFG)
