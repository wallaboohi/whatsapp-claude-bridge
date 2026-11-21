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
    # owner 21.11 02:30: the bedroom is EARLIER and back but not yet stable: it is waited for, the bridge is not passed to a later PC
    assert mod.chain_handback_target(fc, "נייח", view) == ""
    view["חדר שינה"]["up_since"] = now - 120
    assert mod.chain_handback_target(fc, "נייח", view) == "חדר שינה", "stable for a minute: the earliest PC gets it"


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


def test_the_planned_take_works_on_every_backup_not_only_the_bedroom(agent, monkeypatch, tmp_path):
    mod, _ = agent
    for me, before in (("חדר שינה", []), ("סלון", ["חדר שינה"]), ("לפטופ", ["חדר שינה", "סלון"])):
        taken = _holder_env(mod, monkeypatch, tmp_path, planned=me)
        fc = {"for": "נייח", "self_name": me, "defer_to": before}
        assert mod.chain_holder_step(fc, "נייח", {"state": "active"}, HOST_AWAY) is True and len(taken) == 1, me


def test_the_rolling_update_merges_diverged_daily_sync_commits_and_restarts_a_stale_agent():
    import importlib.util, os, sys
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")
    sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location("chain_update_t", os.path.join(tools, "chain_update.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    r = m.REMOTE
    assert "merge hub/main -X theirs" in r and "push hub HEAD:main" in r and "merge --abort" in r, "diverged PCs are merged, a conflict aborts"
    assert "claude/worktrees/" in r, "a scratch worktree gitlink is not dirty code"
    assert "running_head" in r and "$stale" in r, "an agent older than the checkout is restarted even with nothing pulled"


# ---- owner 21.11 00:55 (live round 2): the salon -> laptop hop failed. The bedroom is before the laptop in the chain and was up and
# ready, so the laptop kept answering "an earlier PC is up" and never took the bridge it was handed on purpose.

def test_a_planned_handover_to_this_pc_beats_the_earlier_pc_that_is_ready(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2026-11-21T00:51:40", "by": "salon-pc"})
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    ready = {"agent_alive": True, "holds": [], "lease": {"mode": True, "copy_at": "2026-11-21T00:40:00", "policy": True}}
    view = {"נייח": {"status": "agent_down", "data": None, "last": {}, "since": 1.0},
            "חדר שינה": {"status": "up", "data": ready, "last": {}},
            "סלון": {"status": "up", "data": {**ready, "lease": {**ready["lease"], "held": True}}, "last": {}}}
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now=10_000.0, passed_from="סלון", fenced=("נייח",))
    assert (why, fence) == ("", None), why


def test_without_a_planned_handover_the_earlier_ready_pc_still_has_priority(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    ready = {"agent_alive": True, "holds": [], "lease": {"mode": True, "copy_at": "2026-11-21T00:40:00", "policy": True}}
    view = {"נייח": {"status": "down", "data": None, "last": {}, "since": 1.0},
            "חדר שינה": {"status": "up", "data": ready, "last": {}}}
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now=10_000.0)
    assert why and "לפניי בשרשרת" in why, "the normal order is unchanged when nothing was handed over on purpose"


# ---- owner 21.11 01:10 (live round 3): the salon's agent, standing by, tried to answer old pending commands from the copied history;
# every send failed (its bridge waits at the gate), the run never ended and the restart request waited 6 minutes.

def test_a_standby_serves_only_local_commands(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "lease_standby", lambda: True)
    chats = ["120363431750792742@g.us", mod.LOCAL, "972500000001@s.whatsapp.net"]
    assert mod.serve_chats(chats) == [mod.LOCAL], "no identity here: WhatsApp conversations wait in the queue"


def test_the_holder_serves_every_conversation(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "lease_standby", lambda: False)
    chats = ["120363431750792742@g.us", mod.LOCAL]
    assert mod.serve_chats(chats) == chats


def test_the_dispatcher_does_not_wait_for_work_a_standby_cannot_deliver():
    import inspect
    src = inspect.getsource(__import__("agent").command_worker) if False else None
    import importlib, os, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(here, "agent.py"), encoding="utf-8").read()
    i = text.index("def command_worker():")
    body = text[i:i + 2500]
    assert "handback_overdue() or lease_standby()" in body and "serve_chats(chats)" in body


# ---- owner 21.11 02:00: the laptop renamed the groups to "LAP ..." during a hand-over; the desktop trusted its stale local database
# ("HW ...") and never put the name back.

def _name_env(mod, monkeypatch, db_name, real_name, calls):
    chat = "120363431750792742@g.us"
    monkeypatch.setattr(mod, "sync_group_name_enabled", True, raising=False)
    monkeypatch.setitem(mod.CFG, "group_name_model", True)
    monkeypatch.setattr(mod, "load_groups", lambda: {chat: {"name": "x"}, mod.PRIMARY: {"name": "4M"}})
    monkeypatch.setattr(mod, "guest_cfg", lambda c: False)
    monkeypatch.setattr(mod, "serves_here", lambda c: True)
    monkeypatch.setattr(mod, "lease_standby", lambda: False)
    monkeypatch.setattr(mod, "live_names", lambda: {chat: db_name})
    monkeypatch.setattr(mod, "name_prefix", lambda: "HW")
    monkeypatch.setattr(mod, "group_label", lambda model, c=None: "הייקו")
    monkeypatch.setattr(mod, "group_name_template", lambda c: "HW {model} YB אוטומציה 4M-D")

    def fake_bridge(path, body=None, timeout=180, tok=None):
        calls.append((path, (body or {}).get("name")))
        if path == "/group_info":
            return {"success": True, "name": real_name}
        return {"success": True}
    monkeypatch.setattr(mod, "bridge", fake_bridge)
    return chat


def test_a_wrong_name_in_whatsapp_is_put_back_although_the_local_db_is_stale(agent, monkeypatch):
    mod, _ = agent
    calls = []
    want = "HW הייקו YB אוטומציה 4M-D"
    chat = _name_env(mod, monkeypatch, want, "LAP הייקו YB אוטומציה 4M-D", calls)  # the DB still says HW, WhatsApp says LAP
    mod.kv_set(mod.K("name_set", chat), want)
    mod.sync_group_name(chat, "opus")
    assert ("/set_group", want) in calls, calls


def test_a_correct_name_is_left_alone_and_the_check_is_cached(agent, monkeypatch):
    mod, _ = agent
    calls = []
    want = "HW הייקו YB אוטומציה 4M-D"
    chat = _name_env(mod, monkeypatch, want, want, calls)
    mod.kv_set(mod.K("name_set", chat), want)
    mod.sync_group_name(chat, "opus")
    mod.sync_group_name(chat, "opus")
    assert [c for c in calls if c[0] == "/set_group"] == [], "nothing to fix"
    assert len([c for c in calls if c[0] == "/group_info"]) == 1, "WhatsApp is asked at most every 5 minutes per group"


def test_a_failed_check_is_asked_again_at_the_next_sync(agent, monkeypatch):
    mod, _ = agent
    chat = "120363431750792742@g.us"
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: {"success": False, "error": "connecting"})
    assert mod._verified_live_name(chat) == ""
    assert not mod.kv_get(mod.K("name_verified_at", chat)), "no answer: not marked as verified"


def test_the_grant_forces_every_group_to_be_verified_again(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _lease_env(mod, monkeypatch, tmp_path)
    (tmp_path / "send_policy.json").write_text('{"control_group": "x"}', encoding="utf-8")
    chat = "120363431750792742@g.us"
    monkeypatch.setattr(mod, "load_groups", lambda: {chat: {}})
    mod.kv_set(mod.K("name_verified_at", chat), str(mod.time.time()))
    assert mod.lease_grant("test") is True
    assert mod.kv_get(mod.K("name_verified_at", chat)) == "0", "a bridge that was offline may have missed a rename"


def test_a_scratch_worktree_gitlink_does_not_stop_the_bridge_rebuild(agent):
    mod, _ = agent
    assert mod._code_dirty(" m .claude/worktrees/agent-ad2300d519a4e23fb\n") == "", "the laptop rebuild stopped on exactly this line"
    assert mod._code_dirty(" M whatsapp-bridge/main.go\n m .claude/worktrees/x\n") == " M whatsapp-bridge/main.go", "real changes still count"
    assert mod._code_dirty("") == "" and mod._code_dirty(None) == ""


def test_the_update_rebuilds_a_bridge_exe_that_is_older_than_the_go_code():
    import importlib.util, os, sys
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")
    sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location("chain_update_t2", os.path.join(tools, "chain_update.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    r = m.REMOTE
    assert "stale-exe.go" in r and "LastWriteTimeUtc" in r and "git log -1 --format=%ct" in r, "an old exe is rebuilt even when nothing was pulled"
    assert r.index("stale-exe.go") < r.index("if ($go -and -not $env:DRY)"), "the check comes before the rebuild decision"


def test_the_holder_waits_for_an_unstable_desktop_instead_of_passing_to_the_bedroom(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "lease_held", lambda: True)
    now = time.time()
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    ready = lambda since: {"status": "up", "data": {"lease": {"mode": True, "policy": True}, "chain": {"for": "נייח"}}, "last": {}, "up_since": since}
    # live round 4: the desktop had just woken (8 s), the bedroom was stable: the laptop passed the bridge to the bedroom (62 s offline)
    view = {"נייח": ready(now - 8), "חדר שינה": ready(now - 400)}
    assert mod.chain_handback_target(fc, "נייח", view) == ""
    view["נייח"] = ready(now - 70)
    assert mod.chain_handback_target(fc, "נייח", view) == "נייח", "the desktop is stable: it gets the bridge"


def test_the_fence_backoff_does_not_hold_the_receiver_of_a_handover(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "סלון", "at": "2026-11-21T02:17:20", "by": "PERROOM"})
    fc = {"for": "נייח", "self_name": "סלון", "defer_to": ["חדר שינה"]}
    view = {"נייח": {"status": "agent_down", "data": None, "last": {}, "since": 1.0}}
    mod._chain_recent_unfence["נייח"] = time.time() - 30  # a fence 30 s ago (live round 4: take came at fence + 90 s)
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now=time.time(), passed_from="חדר שינה")
    assert "נעצר לאחרונה" not in why, why
    mod._chain_recent_unfence["נייח"] = time.time() - 30
    (tmp_path / "handover.json").unlink()  # no planned hand-over any more, nothing passed: the loop protection still holds
    _env(mod, monkeypatch, tmp_path)
    mod._chain_recent_unfence["נייח"] = time.time() - 30
    why, fence = mod.chain_take_blocker(fc, "נייח", view, now=time.time())
    assert "נעצר לאחרונה" in why and fence is None


def test_a_fence_that_was_not_undone_leaves_no_backoff(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2026-11-21T02:17:20", "by": "x"})
    monkeypatch.setattr(mod, "_fo_peer", lambda n: n)
    monkeypatch.setattr(mod, "peer_fence", lambda p, n: True)
    monkeypatch.setattr(mod, "peer_unfence", lambda p, n, why="": (_ for _ in ()).throw(AssertionError("no unfence while planned")))
    monkeypatch.setattr(mod, "_chain_note_wait", lambda *a: None)
    monkeypatch.setattr(mod, "_fo_say", lambda *a: None)
    monkeypatch.setattr(mod, "chain_pass_marker", lambda h: "")
    mod.chain_backup_step(FC, "נייח", {}, _view())
    assert mod._chain_recent_unfence == {}, "no unfence happened, so no backoff is recorded"


def test_an_interrupted_run_marker_is_closed_at_start_and_does_not_block_a_round(agent):
    mod, _ = agent
    mod.kv_set(mod.K("live_steps", "120363412798748056@g.us"), json.dumps({"model": "x", "started": "2026-11-21T02:22:03", "done": False, "secs": 5, "steps": []}))
    mod.kv_set(mod.K("live_steps", "local"), json.dumps({"model": "x", "started": "2026-11-12T07:49:29", "done": True, "secs": 5, "steps": []}))
    assert mod.close_interrupted_runs() == 1
    d = json.loads(mod.kv_get(mod.K("live_steps", "120363412798748056@g.us")))
    assert d["done"] is True and d["interrupted"] == "restart"
    assert mod.close_interrupted_runs() == 0, "nothing left to close"


def test_the_relay_state_ignores_an_old_marker_with_nothing_running():
    import importlib.util, os, sys
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")
    sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location("chain_relay_t3", os.path.join(tools, "chain_relay.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import ast
    ast.parse(m.STATE)
    assert "commands WHERE status = 'running'" in m.STATE and "< 180" in m.STATE, "an unfinished marker blocks only with a command running or when it is young"


# ---- owner 21.11 02:50 (live round 5): the salon -> laptop hop failed: the bedroom had passed the IDENTITY on but still held the host's
# automations until its release restart ended, and "the bedroom already holds the work" blocked the laptop for about a minute.

def _holds_view(bedroom_holds_identity=False):
    ready = {"agent_alive": True, "holds": ["נייח"], "lease": {"mode": True, "policy": True, "held": bedroom_holds_identity}}
    return {"נייח": {"status": "agent_down", "data": None, "last": {}, "since": 1.0},
            "חדר שינה": {"status": "up", "data": ready, "last": {}},
            "סלון": {"status": "up", "data": {"agent_alive": True, "holds": ["נייח"], "lease": {"mode": True, "policy": True, "held": False}}, "last": {}}}


def test_a_pc_that_only_holds_the_automations_does_not_block_a_planned_handover(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2026-11-21T02:48:40", "by": "salon-pc"})
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    why, fence = mod.chain_take_blocker(fc, "נייח", _holds_view(), now=10_000.0, passed_from="סלון", fenced=("נייח",))
    assert (why, fence) == ("", None), why


def test_a_pc_that_holds_the_identity_always_blocks(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path, {"to": "לפטופ", "at": "2026-11-21T02:48:40", "by": "salon-pc"})
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    why, fence = mod.chain_take_blocker(fc, "נייח", _holds_view(bedroom_holds_identity=True), now=10_000.0, passed_from="סלון", fenced=("נייח",))
    assert "מחזיק כבר את העבודה" in why, "never two connections on one identity"


def test_without_a_handover_a_pc_holding_the_work_still_blocks(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _env(mod, monkeypatch, tmp_path)  # no planned hand-over
    fc = {"for": "נייח", "self_name": "לפטופ", "defer_to": ["חדר שינה", "סלון"]}
    why, fence = mod.chain_take_blocker(fc, "נייח", _holds_view(), now=10_000.0, fenced=("נייח",))
    assert "מחזיק כבר את העבודה" in why, "the normal rule is unchanged"


# ---- owner 21.11 03:10 (nightly round): after a planned hand-over the desktop woke and granted itself while the bedroom was still
# connected: two connections on one identity, WhatsApp threw the bedroom off, the bridge was offline ~14 s.

def _host_start_env(mod, monkeypatch, alive):
    granted = []
    monkeypatch.setattr(mod, "session_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(mod, "failover_cfg", lambda: None)
    monkeypatch.setattr(mod, "chain_active", lambda: True)
    monkeypatch.setattr(mod, "lease_enable", lambda: None)
    monkeypatch.setattr(mod, "lease_grant", lambda why="": granted.append(why) or True)
    mod.CFG["peers"] = [{"name": "חדר שינה", "url": "http://127.0.0.1:1", "ssh": "PERROOM"}, {"name": "סלון", "url": "http://127.0.0.1:2", "ssh": "SALON"}]
    monkeypatch.setattr(mod, "peer_alias", lambda p: p.get("ssh"))
    monkeypatch.setattr(mod, "peer_alive", lambda p, timeout=3: alive.get(p["name"]))
    monkeypatch.setattr(mod, "peer_alive_ssh", lambda p, timeout=8: None)
    return granted


def test_the_host_does_not_grant_itself_while_a_peer_holds_the_identity(agent, monkeypatch):
    mod, _ = agent
    granted = _host_start_env(mod, monkeypatch, {"חדר שינה": {"agent_alive": True, "lease": {"held": True}}, "סלון": {"agent_alive": True, "lease": {"held": False}}})
    mod.session_host_start(False, [])  # holds == []: the list of who runs our automations is empty, the identity is not
    assert granted == [], "two connections on one identity: the holder hands it back"


def test_the_host_asks_over_ssh_when_the_tunnel_is_not_up_yet(agent, monkeypatch):
    mod, _ = agent
    granted = _host_start_env(mod, monkeypatch, {})  # tunnels down: no answer through them
    monkeypatch.setattr(mod, "peer_alive_ssh", lambda p, timeout=8: {"agent_alive": True, "lease": {"held": True}} if p["name"] == "חדר שינה" else None)
    mod.session_host_start(False, [])
    assert granted == [], "the host starts before its tunnels: the ssh answer is the one that counts"


def test_the_host_grants_itself_when_nobody_holds_the_identity(agent, monkeypatch):
    mod, _ = agent
    granted = _host_start_env(mod, monkeypatch, {"חדר שינה": {"agent_alive": True, "lease": {"held": False}}, "סלון": None})
    mod.session_host_start(False, [])
    assert granted == ["host start"], "an ordinary restart / crash recovery of the host is unchanged"


# ---- owner 21.11 03:30: the nightly peer backup failed every night on one file: on the salon and the laptop the "folder" of a contact was
# a FILE (a copy into a folder that did not exist yet had created a file with the folder's name), and the mkdir error was ignored.

def test_the_backup_moves_a_file_standing_on_a_folder_name_aside_and_checks_the_result(agent):
    mod, _ = agent
    s = mod.remote_mkdirs_script(["C:/b/peer-X/media/972507720836@s.whatsapp.net", "C:/b/peer-X/media/other"])
    assert "-PathType Leaf" in s and "Move-Item" in s and ".file-in-the-way-" in s, "a file in the way is moved aside, never deleted"
    assert "-PathType Container" in s and "exit 1" in s, "the folder must really exist afterwards, else the backup says so"
    assert "'C:/b/peer-X/media/972507720836@s.whatsapp.net','C:/b/peer-X/media/other'" in s
    assert "Remove-Item" not in s, "nothing is deleted"


def test_the_backup_script_runs_in_powershell_for_a_file_in_the_way(agent, tmp_path):
    import subprocess
    mod, _ = agent
    base = tmp_path / "media"
    base.mkdir()
    squat = base / "972507720836@s.whatsapp.net"
    squat.write_bytes(b"x" * 100)  # the live finding: a file with the folder's name
    script = mod.remote_mkdirs_script([str(squat).replace("\\", "/"), str(base / "fresh").replace("\\", "/")])
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert squat.is_dir() and (base / "fresh").is_dir()
    moved = [p for p in base.iterdir() if p.name.startswith("972507720836@s.whatsapp.net.file-in-the-way-")]
    assert len(moved) == 1 and moved[0].read_bytes() == b"x" * 100, "the squatting file is kept, only moved"


def test_the_backup_creates_the_folders_in_small_groups_that_fit_a_command_line(agent, monkeypatch):
    import base64
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "_ps_remote", lambda alias, script, timeout=180: sent.append(script) or (0, "", ""))
    dirs = [f"C:/Users/walla/WhatsApp-Backup/peer-YB-PC-HW/media/97250{i:07d}@s.whatsapp.net" for i in range(400)]  # the laptop's situation
    mod.mkdirs_on_peer("4-MINE", dirs)
    assert len(sent) == 20, "400 folders in groups of 20"
    for s in sent:
        enc = base64.b64encode(("[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + s).encode("utf-16-le")).decode()
        assert len(enc) < 7500, "one command stays below the Windows command-line limit (about 8000)"
    covered = [d for s in sent for d in dirs if f"'{d}'" in s]
    assert covered == dirs, "every folder is in exactly one group, in order"


def test_a_failed_group_stops_the_backup_with_the_reason(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "_ps_remote", lambda alias, script, timeout=180: (1, "", "not a folder: X"))
    try:
        mod.mkdirs_on_peer("4-MINE", ["C:/a", "C:/b"])
        raise AssertionError("must raise")
    except RuntimeError as e:
        assert "creating the backup folders failed" in str(e) and "not a folder" in str(e)
