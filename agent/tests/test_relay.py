"""Owner 20.11 12:01: the relay restart - a planned hand-over moves the bridge forward along the chain, at once."""
import json

from test_agent import agent  # noqa: F401


def _planned(mod, monkeypatch, tmp_path, to):
    p = tmp_path / "handover.json"
    monkeypatch.setattr(mod, "HANDOVER_FILE", str(p))
    if p.exists():
        p.unlink()
    if to:
        p.write_text(json.dumps({"to": to, "at": mod.dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")


def _up(host="נייח", **extra):
    return {"status": "up", "data": {"lease": {"mode": True}, "chain": {"for": host}, **extra}}


def test_holder_passes_forward_to_the_planned_backup_and_keeps_it_when_named(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    fc = {"for": "נייח", "self_name": "חדר שינה", "defer_to": ["נייח"]}
    view = {"נייח": {"status": "agent_down", "data": {}}, "סלון": _up(), "לפטופ": _up()}
    _planned(mod, monkeypatch, tmp_path, "סלון")
    assert mod.chain_handback_target(fc, "נייח", view) == "סלון"  # forward, not only back
    view["סלון"] = _up(holds=["נייח"])
    assert mod.chain_handback_target(fc, "נייח", view) == ""  # never to a PC that holds something
    _planned(mod, monkeypatch, tmp_path, "חדר שינה")
    view = {"נייח": _up(), "סלון": _up()}
    assert mod.chain_handback_target({**fc, "self_name": "חדר שינה"}, "נייח", view) == ""  # named here: keep, even with the host up
    _planned(mod, monkeypatch, tmp_path, None)
    assert mod.chain_handback_target(fc, "נייח", view) == "נייח"  # no plan: the usual way back to the desktop


def test_planned_receiver_does_not_wait_for_the_restart_grace(agent, monkeypatch, tmp_path):
    mod, _ = agent
    now = mod.time.time()
    view = {"נייח": {"status": "agent_down", "since": now - 5, "data": {}}, "חדר שינה": _up(), "לפטופ": _up()}
    fc = {"for": "נייח", "self_name": "סלון", "defer_to": ["חדר שינה"]}
    _planned(mod, monkeypatch, tmp_path, None)
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now)
    assert fence is None and why  # an unplanned silence: the grace holds
    _planned(mod, monkeypatch, tmp_path, "סלון")
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now)
    assert fence == "נייח"  # planned here: fence the desktop at once
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now, passed_from="חדר שינה", fenced=("נייח",))
    assert (why, fence) == ("", None)  # the giver itself does not block


def test_the_holding_backup_can_pass_the_bridge_on(agent, monkeypatch):
    mod, _ = agent
    spread = []
    monkeypatch.setattr(mod, "failover_cfg", lambda: {"for": "נייח", "self_name": "חדר שינה"})
    monkeypatch.setattr(mod, "failover_state", lambda name: {"state": "active"})
    monkeypatch.setattr(mod, "handover_spread", lambda data: spread.append(data) or [])
    mod.CFG["peers"] = [{"name": "נייח"}, {"name": "סלון"}, {"name": "לפטופ"}]
    mod.kv_set("session_taken_at", "2026-11-20T12:00:00")
    out = mod.handover_cmd("סלון")
    assert "מעביר את הגשר מחדר שינה לסלון" in out and spread[0]["to"] == "סלון"
    mod.kv_set("session_taken_at", "")
    assert "רק מהנייח" in mod.handover_cmd("סלון")  # not holding: refused as before


def test_handover_request_file_runs_the_command_once(agent, monkeypatch, tmp_path):
    mod, _ = agent
    req = tmp_path / "handover.request"
    monkeypatch.setattr(mod, "HANDOVER_REQUEST", str(req))
    calls, sent = [], []
    monkeypatch.setattr(mod, "handover_cmd", lambda arg: calls.append(arg) or "ok")
    monkeypatch.setattr(mod, "send", lambda chat, text, *a, **k: sent.append(text))
    mod.handover_request_check()
    assert calls == []
    req.write_text("סלון", encoding="utf-8")
    import os
    os.utime(req, (mod.time.time(), mod.time.time()))  # the release gate runs the suite with a shifted clock (FAKE_NOW): the request file must be as young as that clock says
    mod.handover_request_check()
    mod.handover_request_check()
    assert calls == ["סלון"] and not req.exists() and len(sent) == 1


def test_backup_code_pull_restarts_only_when_not_holding(agent, monkeypatch, tmp_path):
    """Owner 20.11 12:15 + 12:25: a backup standing by restarts on new code; the PC holding the bridge waits for the relay."""
    mod, _ = agent
    import subprocess as sp
    git = lambda cwd, *a: sp.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a], capture_output=True, text=True)
    host, backup = tmp_path / "host", tmp_path / "backup"
    (host / "agent").mkdir(parents=True)
    git(host, "init", "-q", "-b", "master")
    (host / "agent" / "agent.py").write_text("v1", encoding="utf-8")
    git(host, "add", ".")
    git(host, "commit", "-q", "-m", "1")
    sp.run(["git", "clone", "-q", str(host), str(backup)], capture_output=True)
    (host / "agent" / "agent.py").write_text("v2", encoding="utf-8")
    git(host, "commit", "-q", "-am", "2")
    monkeypatch.setattr(mod, "RESTART_FILE", str(tmp_path / "restart.request"))
    fc = {"mirror": [], "git_pull": [{"path": str(backup), "remote": str(host), "branch": "master", "restart": True}]}
    assert mod.failover_code_pull(fc) == []
    assert (backup / "agent" / "agent.py").read_text(encoding="utf-8") == "v2"
    assert (tmp_path / "restart.request").exists(), "a backup standing by restarts freely (owner 12:25)"
    (tmp_path / "restart.request").unlink()
    (host / "agent" / "agent.py").write_text("v3", encoding="utf-8")
    git(host, "commit", "-q", "-am", "3")
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    assert mod.failover_code_pull(fc) == []
    assert not (tmp_path / "restart.request").exists() and mod.kv_get("code_waiting"), "the PC holding the bridge waits"


def test_scheduled_restart_of_the_holder_goes_through_the_relay(agent, monkeypatch):
    mod, _ = agent
    launched = []
    monkeypatch.setattr(mod, "chain_active", lambda: True)
    monkeypatch.setattr(mod, "session_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd, **k: launched.append(cmd))
    monkeypatch.setattr(mod, "failover_cfg", lambda: None)
    monkeypatch.setattr(mod, "lease_held", lambda: False)
    assert mod.relay_restart("x") == "" and not launched  # the desktop not holding: restart as usual
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    assert mod.relay_restart("שמירה") == "relay" and "chain_relay.py" in " ".join(launched[0]) and "--auto" in launched[0]
    monkeypatch.setattr(mod, "failover_cfg", lambda: {"for": "נייח", "self_name": "סלון"})
    assert mod.relay_restart("x") == "defer"  # a backup holding the host's bridge waits
    monkeypatch.setattr(mod, "lease_held", lambda: False)
    monkeypatch.setattr(mod, "failover_state", lambda n: {"state": "idle"})
    assert mod.relay_restart("x") == ""  # a backup standing by restarts freely
