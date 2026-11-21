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
