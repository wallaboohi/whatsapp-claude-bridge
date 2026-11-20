"""Regression tests for the active provider status and Antigravity API adapter."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from test_agent import agent  # noqa: F401


def test_status_card_tracks_the_provider_that_ran(agent, monkeypatch):
    mod, _ = agent
    chat = "provider-status@g.us"
    monkeypatch.setattr(mod, "active_provider", lambda _chat=None: ("codex", None))
    mod._ctx.provider_used = "groq"
    mod._ctx.failover_used = True
    mod._ctx.failover_why = "מכסה נגמרה"
    mod._ctx.force_model = "qwen/qwen3.8-27b"

    st = mod.StatusMsg("sonnet", chat=chat, wa=False)
    st.set_stage("💬 מנסח תשובה")

    assert st.ai_short.startswith(mod.AI_HE.get("groq") or mod.providers()["groq"]["short"])
    assert st.ai_model == "qwen/qwen3.8-27b"
    assert "מכסה נגמרה" in st.ai_short
    st.finish()


def test_antigravity_trajectory_parser_handles_done_busy_and_waiting(agent):
    mod, _ = agent
    path = Path(mod.HERE).parent / "tools" / "desktop_ai.py"
    spec = importlib.util.spec_from_file_location("desktop_ai_test", path)
    desktop_ai = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop_ai)

    step = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "plannerResponse": {"response": "תשובה"}}
    assert desktop_ai.agy_answer({"status": "CASCADE_RUN_STATUS_IDLE", "trajectory": {"steps": [step]}}) == (True, "תשובה", "")
    assert desktop_ai.agy_answer({"status": "CASCADE_RUN_STATUS_RUNNING", "trajectory": {"steps": [step]}})[0] is False

    waiting = {"type": "CORTEX_STEP_TYPE_COMMAND", "status": "CORTEX_STEP_STATUS_WAITING"}
    done, answer, error = desktop_ai.agy_answer({"status": "CASCADE_RUN_STATUS_IDLE", "trajectory": {"steps": [waiting]}})
    assert (done, answer) == (False, "")
    assert "מחכה לאישור" in error


def test_antigravity_run_has_no_outer_timeout(agent, monkeypatch):
    mod, _ = agent
    seen = {}
    monkeypatch.setattr(mod, "_desktop_ai_cmds", lambda _p, _args: [["ssh", "remote"]])

    def fake_run(_cmd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return SimpleNamespace(returncode=0, stdout='{"ok":true,"answer":"שלום"}', stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod._real_desktop_ai_call({"app": "antigravity"}, ["ask", "antigravity"], timeout=1)
    assert result["ok"] and result["answer"] == "שלום"
    assert seen["timeout"] is None
