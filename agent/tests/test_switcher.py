"""Owner 20.11.2026 21:50 (Switcher): the 21:21-21:40 incident. A planned hand-over left store/handover.json on all four PCs; the host
sat "away" (agent_down), the backups fenced it and unfenced it every 2 s, and the laptop could never take the bridge.
Rules under test: no fence of a host that is away on purpose, no fence/unfence loop, a PC earlier in the chain gets the bridge
back only after stable_seconds up, and a planned hand-over nobody took expires."""
import json
import time

from test_agent import agent  # noqa: F401

FC = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}


def _view(host_status="agent_down", **over):
    v = {"נייח": {"status": host_status, "data": None, "last": {}, "since": 1.0},
         "חדר שינה": {"status": "up", "data": {"holds": ["נייח"], "lease": {"held": True}}, "last": {}},
         "סלון": {"status": "up", "data": {"holds": [], "lease": {"mode": "x"}}, "last": {}}}
    v.update(over)
    return v


def _env(mod, monkeypatch, tmp_path, planned=None):
    f = tmp_path / "handover.json"
    if planned:
        f.write_text(json.dumps(planned), encoding="utf-8")
    monkeypatch.setattr(mod, "HANDOVER_FILE", str(f))
    mod._chain_recent_unfence.clear()
    return f


def test_a_host_away_on_purpose_is_never_fenced(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2026-11-20T21:21:50", "by": "salon-pc"})
    other = {**FC, "self_name": "סלון"}  # the hand-over names the laptop; this PC is not the receiver
    why, fence = mod.chain_take_blocker(other, "נייח", _view(), now=10_000.0)
    assert fence is None, "the desktop is away by a planned hand-over: fencing it is what looped"


def test_the_receiver_fences_but_never_unfences_while_the_handover_is_planned(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2026-11-20T21:21:50", "by": "salon-pc"})
    fences, unfences = [], []
    monkeypatch.setattr(mod, "_fo_peer", lambda n: n)
    monkeypatch.setattr(mod, "peer_fence", lambda p, n: fences.append(n) or True)
    monkeypatch.setattr(mod, "peer_unfence", lambda p, n, why="": unfences.append(n))
    monkeypatch.setattr(mod, "_chain_note_wait", lambda *a: None)
    monkeypatch.setattr(mod, "_fo_say", lambda *a: None)
    monkeypatch.setattr(mod, "chain_pass_marker", lambda h: "")
    for _ in range(5):
        mod.chain_backup_step(FC, "נייח", {}, _view())
    assert unfences == [], "an unfenced desktop reconnects and clashes with the holder (stream replaced)"
    assert len(fences) <= 1


def test_an_undone_fence_is_not_repeated(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)
    fences, unfences = [], []
    monkeypatch.setattr(mod, "_fo_peer", lambda n: n)
    monkeypatch.setattr(mod, "peer_fence", lambda p, n: fences.append(n) or True)
    monkeypatch.setattr(mod, "peer_unfence", lambda p, n, why="": unfences.append(n))
    monkeypatch.setattr(mod, "_chain_note_wait", lambda *a: None)
    monkeypatch.setattr(mod, "_fo_say", lambda *a: None)
    monkeypatch.setattr(mod, "chain_pass_marker", lambda h: "")
    view = _view()  # the desktop's agent is down for a long time, a backup "holds the work"
    for _ in range(40):  # 40 rounds = 80 s of the old 2-second loop
        mod.chain_backup_step(FC, "נייח", {}, view)
    assert fences == ["נייח"] and unfences == ["נייח"], (fences, unfences)


def test_the_fence_may_come_again_after_the_backoff(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)
    mod._chain_recent_unfence["נייח"] = time.time() - 200
    why, fence = mod.chain_take_blocker(FC, "נייח", _view(), now=time.time())
    assert fence == "נייח"


def _alive(since):
    return {"status": "up", "data": {"lease": {"mode": "x"}, "chain": {"for": "נייח"}}, "last": {}, "up_since": since}


def test_handback_waits_for_a_full_minute_up(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    now = time.time()
    assert mod.chain_handback_target({"for": "נייח", "self_name": "סלון", "defer_to": ["חדר שינה"]}, "נייח",
                                     {"נייח": _alive(now - 30)}) == "", "30 s up: the holder keeps the bridge"
    assert mod.chain_handback_target({"for": "נייח", "self_name": "סלון", "defer_to": ["חדר שינה"]}, "נייח",
                                     {"נייח": _alive(now - 61)}) == "נייח"
    assert mod.chain_handback_target({"for": "נייח", "self_name": "סלון", "defer_to": ["חדר שינה"]}, "נייח",
                                     {"נייח": _alive(None)}) == "", "a miss resets the count"


def test_the_earliest_stable_pc_gets_the_bridge_not_a_fresh_one(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    now = time.time()
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    view = {"נייח": {"status": "down", "data": None, "last": {}},
            "חדר שינה": _alive(now - 10),   # earlier, but back only 10 s ago
            "סלון": _alive(now - 300)}      # later, stable
    view["חדר שינה"]["data"]["chain"] = {"for": "נייח"}
    assert mod.chain_handback_target(fc, "נייח", view) == "סלון"


def test_a_planned_handover_nobody_took_expires(agent, monkeypatch, tmp_path):
    mod, _ = agent
    f = _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2020-01-01T00:00:00", "by": "salon-pc"})
    monkeypatch.setattr(mod, "failover_cfg", lambda: FC)
    monkeypatch.setattr(mod, "lease_held", lambda: False)
    assert mod.handover_expire({"לפטופ": {"status": "up", "data": {"lease": {"held": False}}}}) is True
    assert not f.exists() and list(tmp_path.glob("handover.json.expired-*")), "moved aside, never deleted"


def test_a_handover_the_target_holds_stays(agent, monkeypatch, tmp_path):
    mod, _ = agent
    f = _env(mod, monkeypatch, tmp_path, {"to": "חדר שינה", "at": "2020-01-01T00:00:00", "by": "owner"})
    monkeypatch.setattr(mod, "failover_cfg", lambda: FC)
    monkeypatch.setattr(mod, "lease_held", lambda: False)
    v = {"חדר שינה": {"status": "up", "data": {"lease": {"held": True}}}}
    assert mod.handover_expire(v) is False and f.exists(), "the owner parked the bridge there on purpose"


def test_a_young_handover_is_left_alone(agent, monkeypatch, tmp_path):
    mod, _ = agent
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    f = _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": at, "by": "x"})
    monkeypatch.setattr(mod, "failover_cfg", lambda: FC)
    assert mod.handover_expire({"לפטופ": {"status": "up", "data": {"lease": {"held": False}}}}) is False and f.exists()

