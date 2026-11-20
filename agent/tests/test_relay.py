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
    mod.handover_request_check()
    mod.handover_request_check()
    assert calls == ["סלון"] and not req.exists() and len(sent) == 1
