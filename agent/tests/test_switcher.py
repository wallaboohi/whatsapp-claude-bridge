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



# ---- owner 20.11 23:45: the draft storm. A PC without store/send_policy.json ran the bridge's default policy: every reply to the
# owner's groups became a "טיוטה" card in his personal chat. The policy must travel, and a PC without it must not hold the bridge.

def _lease_env(mod, monkeypatch, tmp_path, peers=None):
    monkeypatch.setattr(mod, "SEND_POLICY", str(tmp_path / "send_policy.json"))
    monkeypatch.setattr(mod, "STORE", str(tmp_path))
    monkeypatch.setattr(mod, "LEASE_GRANT", str(tmp_path / "lease_grant"))
    monkeypatch.setattr(mod, "LEASE_MODE", str(tmp_path / "lease_mode"))
    monkeypatch.setattr(mod, "session_cfg", lambda: {"enabled": True})
    mod.CFG["peers"] = peers or [{"name": "נייח", "ssh": "DESK"}]
    monkeypatch.setattr(mod, "peer_alias", lambda p: p.get("ssh"))


class _R:
    def __init__(self, text, rc=0):
        self.stdout, self.returncode = text.encode("utf-8"), rc


def test_no_grant_without_a_send_policy(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_ssh", lambda *a, **k: _R("", 1))  # no peer can give one
    assert mod.lease_grant("test") is False
    assert not (tmp_path / "lease_grant").exists(), "the bridge must not connect here: it would draft into the owner's personal chat"


def test_the_policy_is_copied_from_a_peer_and_then_the_grant_works(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_ssh", lambda *a, **k: _R('{"mode": "draft", "control_group": "x@g.us"}'))
    assert mod.lease_grant("test") is True
    assert (tmp_path / "send_policy.json").read_text(encoding="utf-8").count("control_group") == 1
    # byte for byte: a LF file stays LF (the live test 21.11 00:11 found CR added on Windows)
    monkeypatch.setattr(mod, "_ssh", lambda *a, **k: _R('{"mode": "draft",\n "control_group": "x@g.us"}\n'))
    (tmp_path / "send_policy.json").unlink()
    assert mod.ensure_send_policy() is True
    assert b"\r" not in (tmp_path / "send_policy.json").read_bytes()
    assert (tmp_path / "lease_grant").exists()


def test_an_empty_shell_policy_is_not_copied(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_ssh", lambda *a, **k: _R('{"mode": "draft"}'))
    assert mod.ensure_send_policy() is False and not (tmp_path / "send_policy.json").exists()


def test_an_existing_policy_is_never_overwritten(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    (tmp_path / "send_policy.json").write_text('{"control_group": "mine"}', encoding="utf-8")
    monkeypatch.setattr(mod, "_ssh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ssh needed")))
    assert mod.ensure_send_policy() is True
    assert "mine" in (tmp_path / "send_policy.json").read_text(encoding="utf-8")


def test_without_lease_mode_nothing_changes(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "session_cfg", lambda: None)
    assert mod.ensure_send_policy() is True


def test_the_hand_over_carries_the_policy_and_never_overwrites_the_receivers(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    (tmp_path / "send_policy.json").write_text('{"control_group": "holder"}', encoding="utf-8")
    monkeypatch.setattr(mod, "_sqlite_bytes", lambda p: b"x")
    assert "holder" in mod._session_handback_payload("")["send_policy"]
    store = tmp_path / "target"
    store.mkdir()
    C = {"store": str(store).replace("\\", "/"), "session": "", "mode": "host", "send_policy": '{"control_group": "holder"}', "agent_db": ""}
    import base64
    C["session"] = base64.b64encode(b"x").decode()
    run = lambda: exec(compile(mod.SESSION_HANDBACK, "hb", "exec"), {"C": dict(C)})
    run()
    assert "holder" in (store / "send_policy.json").read_text(encoding="utf-8")
    (store / "send_policy.json").write_text('{"control_group": "receiver"}', encoding="utf-8")
    run()
    assert "receiver" in (store / "send_policy.json").read_text(encoding="utf-8"), "the receiver's own policy wins"


def test_a_pc_without_a_policy_is_neither_ready_nor_a_handback_target(agent, monkeypatch, tmp_path):
    mod, _ = agent
    good = {"agent_alive": True, "lease": {"mode": True, "copy_at": "2026-11-20T21:00:00", "policy": True}}
    bad = {"agent_alive": True, "lease": {"mode": True, "copy_at": "2026-11-20T21:00:00", "policy": False}}
    assert mod._chain_ready(good) and not mod._chain_ready(bad)
    old = {"agent_alive": True, "lease": {"mode": True, "copy_at": "2026-11-20T21:00:00"}}
    assert mod._chain_ready(old), "an older peer that does not report the key is not blocked"
    _env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    fc = {"for": "נייח", "self_name": "סלון", "defer_to": ["חדר שינה"]}
    view = {"נייח": {"status": "up", "data": {**bad, "chain": {"for": "נייח"}}, "last": {}}}
    assert mod.chain_handback_target(fc, "נייח", view) == "", "the bridge never goes to a PC without a send policy"


# ---- owner 21.11 00:26: the live round failed. The host's agent restarted, this backup covered the restart (work, no identity), the
# host then released the identity for the planned hand-over, and nobody took it: the bridge sat offline for ~5 minutes.

def _holder_env(mod, monkeypatch, tmp_path, planned="חדר שינה", holding=False):
    _env(mod, monkeypatch, tmp_path, {"to": planned, "at": "2026-11-21T00:25:55", "by": "YB-PC-HW"} if planned else None)
    monkeypatch.setattr(mod, "lease_held", lambda: False)
    monkeypatch.setattr(mod, "lease_replaced", lambda *a: False)
    monkeypatch.setattr(mod, "kv_get", lambda k, d=None: ("2026-11-21T00:25:53" if (holding and k == "session_taken_at") else ""))
    monkeypatch.setattr(mod, "kv_set", lambda *a: None)
    taken = []
    monkeypatch.setattr(mod, "session_take", lambda why: taken.append(why) or "ok")
    return taken


HOLDER_FC = {"for": "נייח", "self_name": "חדר שינה", "defer_to": []}
HOST_AWAY = {"נייח": {"status": "agent_down", "data": None, "last": {}, "since": 1.0}}


def test_a_planned_handover_takes_the_identity_when_the_work_is_already_here(agent, monkeypatch, tmp_path):
    mod, _ = agent
    taken = _holder_env(mod, monkeypatch, tmp_path)
    assert mod.chain_holder_step(HOLDER_FC, "נייח", {"state": "active"}, HOST_AWAY) is True
    assert len(taken) == 1, "nobody holds the identity and it was handed to this PC: take it"


def test_the_identity_is_not_taken_while_someone_still_holds_it(agent, monkeypatch, tmp_path):
    mod, _ = agent
    taken = _holder_env(mod, monkeypatch, tmp_path)
    view = {"נייח": {"status": "up", "data": {"lease": {"held": True}}, "last": {}}}
    mod.chain_holder_step(HOLDER_FC, "נייח", {"state": "active"}, view)
    assert taken == [], "never two connections: the desktop has not released yet"


def test_no_take_when_the_handover_names_another_pc_or_none(agent, monkeypatch, tmp_path):
    mod, _ = agent
    for planned in ("סלון", None):
        taken = _holder_env(mod, monkeypatch, tmp_path, planned=planned)
        mod.chain_holder_step(HOLDER_FC, "נייח", {"state": "active"}, HOST_AWAY)
        assert taken == [], planned


def test_a_pc_that_already_took_the_identity_is_left_alone(agent, monkeypatch, tmp_path):
    mod, _ = agent
    taken = _holder_env(mod, monkeypatch, tmp_path, holding=True)
    mod.chain_holder_step(HOLDER_FC, "נייח", {"state": "active"}, HOST_AWAY)
    assert taken == []
