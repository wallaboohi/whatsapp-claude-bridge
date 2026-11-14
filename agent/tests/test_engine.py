"""Owner 14.11: the agent engine layer and the failover that keeps the bridge answering when Claude's usage quota is used up."""
import json

from test_agent import agent  # noqa: F401


def _keys(mod, monkeypatch, *names):
    have = set(names)
    monkeypatch.setattr(mod, "provider_key", lambda p: "k" if p.get("key_file", "").removesuffix(".key") in have else "")


def _no_state_files(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    monkeypatch.setattr(mod, "FAILOVER_FILE", str(tmp_path / "failover.json"))
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))


def test_engine_choice_and_failover_settings(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _no_state_files(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "engine_ready", lambda n: n in ("claude", "qwen"))
    assert mod.active_engine() == "claude" and mod.limit_failover_cfg() == {"on_limit": True, "engine": "qwen"}
    assert "מנוע הגשר: *Qwen Code*" in mod.owner_command("/מנוע qwen", mod.PRIMARY) and mod.active_engine() == "qwen"
    assert "לא מכיר" in mod.owner_command("/מנוע nope", mod.PRIMARY)
    monkeypatch.setattr(mod, "engine_ready", lambda n: n == "claude")
    assert "לא מותקן" in mod.owner_command("/מנוע qwen", mod.PRIMARY)
    mod.owner_command("/גיבוי לא", mod.PRIMARY)
    assert mod.limit_failover_cfg()["on_limit"] is False and mod.failover_target() is None
    mod.owner_command("/גיבוי כן", mod.PRIMARY)
    mod.owner_command("/גיבוי claude", mod.PRIMARY)
    assert mod.limit_failover_cfg() == {"on_limit": True, "engine": "claude"}
    assert "👉 *Claude Code*" in mod.owner_command("/מנוע", mod.PRIMARY) or "מנוע הסוכן" in mod.owner_command("/מנוע", mod.PRIMARY)


def test_failover_target_picks_the_first_ready_free_provider_and_the_chosen_engine(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _no_state_files(mod, monkeypatch, tmp_path)
    _keys(mod, monkeypatch, "gemini", "groq")
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(hours=1)).isoformat(timespec="seconds"))
    monkeypatch.setattr(mod, "engine_ready", lambda n: n in ("claude", "qwen"))
    assert mod.failover_target() == ("qwen", "gemini")  # the subscription is capped: the first free provider, on Qwen Code
    mod.auto_cool("gemini", 10, "rate")
    assert mod.failover_target() == ("qwen", "groq")
    monkeypatch.setattr(mod, "engine_ready", lambda n: n == "claude")
    assert mod.failover_target() == ("claude", "groq")  # Qwen Code is not installed: Claude Code with that provider
    monkeypatch.setattr(mod, "provider_ready", lambda n: n == "ollama")
    assert mod.failover_target() == ("claude", "ollama")  # nothing but the local model is left
    monkeypatch.setattr(mod, "provider_ready", lambda n: False)
    assert mod.failover_target() is None  # nobody: then the old behaviour (wait for the quota)


def test_a_capped_claude_does_not_stop_the_bridge(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _no_state_files(mod, monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "failover_target", lambda: ("qwen", "groq"))
    seen = {}

    def fake(prompt, model, sid, *a, **k):
        seen.update(engine=getattr(mod._ctx, "force_engine", None), provider=getattr(mod._ctx, "force_provider", None), sid=sid)
        mod._ctx.engine_used, mod._ctx.provider_used = "qwen", mod._ctx.force_provider  # what the real _run_claude records
        return {"result": "תשובה מהמנוע החלופי", "session_id": "qwen-session-1"}
    monkeypatch.setattr(mod, "_run_claude", fake)
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(hours=1)).isoformat(timespec="seconds"))
    mod.kv_set(mod.K("session_id", mod.PRIMARY), "claude-session-1")
    out = mod.claude("שלום", model="opus", on_stage=lambda *a: None, chat=mod.PRIMARY)  # an interactive run: it used to raise LimitHit
    assert out == "תשובה מהמנוע החלופי" and seen["engine"] == "qwen" and seen["provider"] == "groq"
    assert mod.kv_get(mod.K("session_id", mod.PRIMARY)) == "claude-session-1"  # the other engine's session never replaces Claude's
    assert "מכסת Claude נגמרה" in mod.ai_tag("opus") and "מנוע Qwen Code" in mod.ai_tag("opus") and "Groq" in mod.ai_tag("opus")
    assert getattr(mod._ctx, "force_provider", None) is None  # the override does not leak into the next run


def test_claude_failing_mid_run_moves_to_the_failover_and_then_to_the_next_provider(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _no_state_files(mod, monkeypatch, tmp_path)
    _keys(mod, monkeypatch, "gemini", "groq")
    monkeypatch.setattr(mod, "engine_ready", lambda n: n == "claude")
    calls = []

    def fake(*a, **k):
        prov = getattr(mod._ctx, "force_provider", None)
        mod._ctx.provider_used = prov or "claude"
        calls.append(prov)
        if prov is None:
            raise mod.LimitHit(mod.dt.datetime.now() + mod.dt.timedelta(hours=1))  # Claude: quota
        if prov == "gemini":
            raise mod.ProviderDown("gemini", "rate", 15)  # the first free provider is busy
        return {"result": "ok"}
    monkeypatch.setattr(mod, "_run_claude", fake)
    assert mod._run_auto("p", None, None, 5) == {"result": "ok"}
    assert calls == [None, "gemini", "groq"] and mod.provider_cooled("gemini")


def test_guests_and_restricted_runs_keep_claude_code(agent, monkeypatch, tmp_path):
    mod, _ = agent
    _no_state_files(mod, monkeypatch, tmp_path)
    mod.set_engine("qwen")
    monkeypatch.setattr(mod, "engine_ready", lambda n: True)
    started = []

    class Boom(Exception):
        pass

    def popen(cmd, **k):
        started.append(cmd[0])
        raise Boom()
    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    for kw in ({"tools": ""},):  # a restricted run (classifying other people's text)
        try:
            mod._run_claude("x", "haiku", None, 5, **kw)
        except Boom:
            pass
    assert started and "claude" in started[0].lower() and "qwen" not in started[0].lower()
    assert mod._ctx.engine_used == "claude"


def test_qwen_command_uses_the_provider_endpoint_without_a_translator(agent, monkeypatch):
    mod, _ = agent
    _keys(mod, monkeypatch, "groq")
    cmd, env, m = mod.qwen_cmd_env("groq", None, "ענה בעברית")
    assert cmd[1:3] == ["-y", "-o"] and "--append-system-prompt" in cmd and m == "openai/gpt-oss-120b"
    assert env["OPENAI_BASE_URL"] == "https://api.groq.com/openai/v1" and env["OPENAI_API_KEY"] == "k" and env["OPENAI_MODEL"] == m
    monkeypatch.setattr(mod, "provider_host", lambda p, m=None, max_age=20: {"url": "http://127.0.0.1:11435", "label": "לפטופ"})
    cmd, env, m = mod.qwen_cmd_env("ollama", "bridge-qwen3-4b:latest", None)
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:11435/v1" and m == "bridge-qwen3-4b:latest"
    try:
        mod.qwen_cmd_env("claude", None, None)
        assert False, "the Claude subscription has no OpenAI endpoint"
    except RuntimeError as e:
        assert "OpenAI" in str(e)


def test_the_free_tier_and_retired_model_errors_move_the_failover_on(agent):
    mod, _ = agent
    c = mod.classify_down
    assert c("[API Error: 413 Request too large for model `openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000, Requested 14123]")[0] == "rate"
    assert c("[API Error: 410 status code (no body)]")[0] == "rate"  # a retired model: skipped for a while, the next provider takes over
