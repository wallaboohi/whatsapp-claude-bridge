"""Owner 20.11 13:00: learned from the relay failures of 12:51 and 12:56 - the relay is never killed with an ssh session, the
holder never hands over from old code, and a stale hand-over request never moves the bridge by surprise."""
import importlib.util
import os
import time

from test_agent import agent  # noqa: F401

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")


def _relay():
    import sys
    sys.path.insert(0, TOOLS)
    spec = importlib.util.spec_from_file_location("chain_relay_t", os.path.join(TOOLS, "chain_relay.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_relay_runs_in_place_when_not_started_over_ssh(monkeypatch):
    r = _relay()
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    assert r.detach_from_ssh([]) == 0
    monkeypatch.setenv("SSH_CONNECTION", "1 2 3 4")
    assert r.detach_from_ssh(["--detached"]) == 0, "the detached copy never detaches again"


def test_relay_started_over_ssh_relaunches_itself_outside_the_session(monkeypatch):
    r = _relay()
    monkeypatch.setenv("SSH_CONNECTION", "1 2 3 4")
    monkeypatch.setattr(r.os, "name", "nt")
    seen = {}

    class R:
        stdout, stderr = "0 4242", ""
    monkeypatch.setattr(r.subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd) or R())
    assert r.detach_from_ssh(["--reason", "יישור קו"]) == 4242
    assert "Win32_Process" in seen["cmd"][-1] and "--args-b64" in seen["cmd"][-1]


def test_holder_agent_moves_to_the_new_code_before_the_hand_over(monkeypatch):
    r = _relay()
    states = iter([{"running_head": "7da6be7", "head": "8dcf460", "lease": "holding x"},
                   {"running_head": "7da6be7", "head": "8dcf460", "lease": "holding x"},
                   {"running_head": "8dcf460", "head": "8dcf460", "lease": "holding y"}])
    monkeypatch.setattr(r, "state", lambda name: next(states))
    asked = []
    monkeypatch.setattr(r, "run_py", lambda name, code, timeout=0: asked.append(code) or (0, "@@OK@@"))
    monkeypatch.setattr(r.time, "sleep", lambda s: None)
    ok, why = r.ensure_holder_code("נייח", "8dcf460")
    assert ok and "restart.request" in asked[0] and "נשאר מחובר" in why
    monkeypatch.setattr(r, "state", lambda name: {"running_head": "8dcf460", "head": "8dcf460", "lease": "holding"})
    assert r.ensure_holder_code("נייח", "8dcf460")[0]


def test_a_stale_hand_over_request_is_ignored(agent, monkeypatch, tmp_path):
    mod, _ = agent
    req = tmp_path / "handover.request"
    req.write_text("חדר שינה", encoding="utf-8")
    old = time.time() - 3600
    os.utime(req, (old, old))
    monkeypatch.setattr(mod, "HANDOVER_REQUEST", str(req))
    moved = []
    monkeypatch.setattr(mod, "handover_cmd", lambda arg: moved.append(arg) or "ok")
    mod.handover_request_check()
    assert moved == [] and not req.exists()
    req.write_text("חדר שינה", encoding="utf-8")
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    mod.handover_request_check()
    assert moved == ["חדר שינה"]


def test_the_detach_command_parses_in_powershell(monkeypatch):
    """12:58: the first version passed JSON-quoted paths and PowerShell refused it. The real command is parsed here."""
    import shutil
    import subprocess as sp
    if not shutil.which("powershell"):
        return
    real_run = sp.run  # the same module object is patched below
    r = _relay()
    monkeypatch.setenv("SSH_CONNECTION", "1 2 3 4")
    monkeypatch.setattr(r.os, "name", "nt")
    seen = {}

    class R:
        stdout, stderr = "0 1", ""
    monkeypatch.setattr(r.subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd) or R())
    r.detach_from_ssh(["--reason", "יישור קו 'בדיקה'"])  # parse only below: no process is ever started
    check = "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput(" +         "'" + seen["cmd"][-1].replace("'", "''") + "', [ref]$null, [ref]$e); if ($e.Count) { exit 1 } else { exit 0 }"
    assert real_run(["powershell", "-NoProfile", "-Command", check], capture_output=True).returncode == 0


def test_nightly_uses_one_backup_and_the_ordered_run_is_the_full_round():
    r = _relay()
    assert r.plan_hops(True) == [("נייח", "חדר שינה"), ("חדר שינה", "נייח")], "nightly: one backup holds, then back"
    full = r.plan_hops(False)
    assert [g for g, _ in full] == ["נייח", "חדר שינה", "סלון", "לפטופ"] and full[-1] == ("לפטופ", "נייח"), "ordered: all four, back to the desktop"


def _clock(r, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(r.time, "time", lambda: t[0])
    monkeypatch.setattr(r.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    return t


def test_the_desktop_gets_the_bridge_back_only_after_a_full_minute_of_activity(monkeypatch):
    r = _relay()
    _clock(r, monkeypatch)
    monkeypatch.setattr(r, "state", lambda n: {"running_head": "abc1234", "lease": "waiting 21:00"})
    ok, why = r.wait_stable("נייח", "abc1234", 60)
    assert ok and "60" in why


def test_a_failed_check_restarts_the_minute(monkeypatch):
    r = _relay()
    t = _clock(r, monkeypatch)
    seen = []

    def st(n):
        seen.append(t[0])
        if len(seen) == 8:  # one bad answer 35 s in
            return {"error": "no answer"}
        return {"running_head": "abc1234", "lease": "waiting"}
    monkeypatch.setattr(r, "state", st)
    ok, _ = r.wait_stable("נייח", "abc1234", 60)
    assert ok and t[0] - 1000.0 >= 35 + 60, "the count restarted after the failed check"


def test_old_code_or_a_bridge_not_waiting_never_counts_as_stable(monkeypatch):
    r = _relay()
    _clock(r, monkeypatch)
    monkeypatch.setattr(r, "state", lambda n: {"running_head": "old0000", "lease": "waiting"})
    assert r.wait_stable("נייח", "abc1234", 60, timeout=120)[0] is False
    monkeypatch.setattr(r, "state", lambda n: {"running_head": "abc1234", "lease": "holding x"})
    assert r.wait_stable("נייח", "abc1234", 60, timeout=120)[0] is False


def test_the_tool_check_fails_for_a_pc_without_a_send_policy(monkeypatch):
    r = _relay()
    monkeypatch.setattr(r, "state", lambda n: {"policy": False, "lease": "waiting", "busy": [], "head": "abc"})
    ok, why = r.tool_check("חדר שינה", False)
    assert not ok and "send_policy.json" in why
    ok, why = r.tool_check("חדר שינה", True)
    assert not ok and "send_policy.json" in why
    monkeypatch.setattr(r, "state", lambda n: {"policy": True, "lease": "waiting", "busy": [], "head": "abc"})
    assert r.tool_check("חדר שינה", False)[0]


def test_a_scratch_worktree_gitlink_is_not_dirty_code():
    r = _relay()
    assert ".claude/worktrees/" in r.STATE and 'not in l' in r.STATE
    import ast
    ast.parse(r.STATE)  # the snippet that runs on every PC must stay valid Python


def test_a_failed_hop_brings_the_desktop_back(monkeypatch):
    r = _relay()
    ran = []
    monkeypatch.setattr(r, "holders", lambda: [])
    monkeypatch.setattr(r, "run_py", lambda n, code, timeout=0: ran.append(n) or (0, "@@OK@@"))
    monkeypatch.setattr(r, "wait_holding", lambda n, m=5: n == "נייח")
    assert r.recover_desktop("נייח -> חדר שינה") is True
    assert ran[-1] == "נייח" and set(ran) == set(r.ORDER), "the hand-over file is moved aside on all four, the desktop last"


def test_no_recovery_when_someone_holds_the_bridge(monkeypatch):
    r = _relay()
    monkeypatch.setattr(r, "holders", lambda: ["חדר שינה"])
    monkeypatch.setattr(r, "run_py", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not touch anything")))
    assert r.recover_desktop("x") is False
