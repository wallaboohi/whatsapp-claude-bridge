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
