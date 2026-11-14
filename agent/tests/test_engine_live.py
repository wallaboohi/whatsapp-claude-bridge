"""A REAL run through the agent with the Qwen Code engine (no mocks): skipped unless LIVE_ENGINE=1 (it calls a free remote provider; needs secrets/nvidia.key and `qwen`)."""
import os
import shutil

import pytest

from test_agent import agent  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get("LIVE_ENGINE") != "1" or not shutil.which("qwen"), reason="set LIVE_ENGINE=1 (and install qwen) to run")


def test_the_bridge_answers_through_qwen_code_when_claude_is_capped(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    monkeypatch.setattr(mod, "FAILOVER_FILE", str(tmp_path / "failover.json"))
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))
    monkeypatch.setattr(mod, "provider_ready", lambda n: n == "nvidia")
    monkeypatch.setattr(mod, "auto_pick", lambda skip=(): "nvidia")
    mod.CFG.setdefault("language", "he")
    os.makedirs(mod.HOME, exist_ok=True)
    open(os.path.join(mod.HOME, "note.txt"), "w", encoding="utf-8").write("The secret code is ZEBRA-4471\n")
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(hours=1)).isoformat(timespec="seconds"))  # Claude: quota used up
    mod.set_failover(on_limit=True, engine="qwen")
    stages = []
    out = mod.claude("Read the file note.txt in the current directory and reply with only the secret code it contains.", session=False, timeout=300,
                     chat=mod.PRIMARY, on_stage=lambda *a: stages.append(a))
    assert out and "ZEBRA-4471" in out, out
    assert mod._ctx.engine_used == "qwen" and mod._ctx.failover_used is True and mod._ctx.provider_used == "nvidia"
    tag = mod.ai_tag("opus")
    assert "NVIDIA" in tag and "Qwen Code" in tag and "מכסת Claude נגמרה" in tag


def test_a_hosted_ai_reads_the_bridge_through_the_whatsapp_tools(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    monkeypatch.setattr(mod, "FAILOVER_FILE", str(tmp_path / "failover.json"))
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))
    monkeypatch.setattr(mod, "provider_ready", lambda n: n == "nvidia")
    monkeypatch.setattr(mod, "auto_pick", lambda skip=(): "nvidia")
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(hours=1)).isoformat(timespec="seconds"))
    mod.set_failover(on_limit=True, engine="qwen")
    out = mod.claude("Use the whatsapp tool get_status and reply with only the bridge version number it reports.", session=False, timeout=300,
                     chat=mod.PRIMARY, on_stage=lambda *a: None)
    assert out and "1.1" in out, out  # the connection itself gave the tool: the live bridge reports 1.1.x


def test_a_broken_chosen_ai_is_replaced_and_the_answer_says_why(agent, monkeypatch, tmp_path):
    """Owner 14.11: a conversation whose chosen AI does not work still gets an answer, from the next ready one, with the reason."""
    mod, _ = agent
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    monkeypatch.setattr(mod, "FAILOVER_FILE", str(tmp_path / "failover.json"))
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))
    real = mod.providers()
    broken = {"label": "Broken", "short": "Broken", "kind": "anthropic", "base_url": "http://127.0.0.1:9", "token": "x", "model": "none", "where": "remote"}
    monkeypatch.setattr(mod, "providers", lambda: {**real, "broken": broken})
    monkeypatch.setattr(mod, "auto_pick", lambda skip=(): "nvidia" if "broken" in skip else "broken")
    mod.CFG.setdefault("language", "he")
    os.makedirs(mod.HOME, exist_ok=True)
    open(os.path.join(mod.HOME, "note.txt"), "w", encoding="utf-8").write("The secret code is ZEBRA-4471\n")
    mod.set_failover(on_limit=True, engine="qwen")
    mod.set_provider("broken", chat=mod.PRIMARY)
    mod._ctx.chat = mod.PRIMARY
    try:
        out = mod.claude("Read the file note.txt in the current directory and reply with only the secret code it contains.", session=False, timeout=300,
                         chat=mod.PRIMARY, on_stage=lambda *a: None)
        assert out and "ZEBRA-4471" in out, out
        assert mod._ctx.provider_used == "nvidia" and mod._ctx.failover_used is True
        assert "🔁 Broken לא עבד" in mod.ai_tag("opus")
    finally:
        mod._ctx.chat = None


def test_opencode_answers_a_conversation_with_tools(agent, monkeypatch, tmp_path):
    """Owner 14.11: OpenCode (installed as an app) is a free engine+provider: a real run reads a file and answers in Hebrew."""
    mod, _ = agent
    oc = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "ai.opencode.desktop", "resources", "opencode-cli.exe")
    if not os.path.isfile(oc):
        pytest.skip("OpenCode is not installed")
    monkeypatch.setattr(mod, "opencode_bin", lambda: oc)
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    monkeypatch.setattr(mod, "FAILOVER_FILE", str(tmp_path / "failover.json"))
    mod.CFG.setdefault("language", "he")
    os.makedirs(mod.HOME, exist_ok=True)
    open(os.path.join(mod.HOME, "note.txt"), "w", encoding="utf-8").write("The secret code is ZEBRA-4471\n")
    mod.set_provider("opencode", chat=mod.PRIMARY)
    mod._ctx.chat = mod.PRIMARY
    stages = []
    try:
        out = mod.claude("קרא את הקובץ note.txt שבתיקייה הנוכחית (" + mod.HOME + ") וכתוב מה כתוב בו.", session=False, timeout=300,
                         chat=mod.PRIMARY, on_stage=lambda *a: stages.append(a))
        assert out and "ZEBRA-4471" in out, (mod.HOME, mod._ctx.engine_used, getattr(mod._ctx, "provider_used", None), out[:300])
        assert mod._ctx.engine_used == "opencode" and mod._ctx.provider_used == "opencode"
        assert any("קורא" in str(s) for s in stages), stages
        assert mod.ai_tag().startswith("🧠 OpenCode")
    finally:
        mod._ctx.chat = None


@pytest.mark.parametrize("prov", ["gemini_cli", "codex", "copilot"])
def test_agent_cli_answers_a_conversation(agent, monkeypatch, tmp_path, prov):
    """Owner 14.11: the agent CLIs installed on this PC answer a conversation through the bridge (real run, reads a file)."""
    mod, _ = agent
    monkeypatch.setattr(mod, "cli_ready", lambda p: bool(shutil.which((p.get("cli") or {}).get("bin") or "")))
    if not mod.cli_ready(mod.providers()[prov]):
        pytest.skip(prov + " is not installed")
    monkeypatch.setattr(mod, "FAILOVER_FILE", str(tmp_path / "failover.json"))
    os.makedirs(mod.HOME, exist_ok=True)
    open(os.path.join(mod.HOME, "note.txt"), "w", encoding="utf-8").write("Fruit of the day: ZEBRA-4471\n")
    mod.set_provider(prov, chat=mod.PRIMARY)
    mod._ctx.chat = mod.PRIMARY
    stages = []
    try:
        out = mod.claude("קרא את הקובץ note.txt שבתיקייה הנוכחית (" + mod.HOME + ") וכתוב מה כתוב בו.", session=False, timeout=300,
                         chat=mod.PRIMARY, on_stage=lambda *a: stages.append(a))
        assert out and "ZEBRA-4471" in out, out
        assert mod._ctx.engine_used == "cli" and mod._ctx.provider_used == prov and stages
    finally:
        mod._ctx.chat = None
