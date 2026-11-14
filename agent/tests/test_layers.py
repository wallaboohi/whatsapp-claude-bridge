"""Owner 14.11: the map's layer windows: engine, failover, restarts, provider models (the dashboard side)."""
import json
import os

from test_dash import dash  # noqa: F401


def test_engine_and_failover_are_set_from_the_map(dash, monkeypatch):
    srv, root = dash
    monkeypatch.setattr(srv.shutil, "which", lambda c: "C:/x/" + c if c in ("claude", "qwen") else None)
    info = srv.engine_info()
    assert info["engine"] == "claude" and info["ready"] == {"claude": True, "qwen": True} and info["failover"] == {"on_limit": True, "engine": "qwen"}
    assert srv.action({"do": "engine_set", "engine": "qwen"})["ok"]
    assert json.loads((root / "agent" / "engine.json").read_text(encoding="utf-8"))["engine"] == "qwen" and srv.engine_info()["engine"] == "qwen"
    assert srv.action({"do": "engine_set", "engine": "nope"})["error"] and srv.action({"do": "failover_set", "engine": "nope", "on_limit": False})["failover"]["on_limit"] is False
    assert json.loads((root / "agent" / "failover.json").read_text(encoding="utf-8")) == {"on_limit": False, "engine": "qwen"}
    assert srv.action({"do": "failover_set", "on_limit": True, "engine": "claude"})["failover"] == {"on_limit": True, "engine": "claude"}
    monkeypatch.setattr(srv.shutil, "which", lambda c: "C:/x/claude" if c == "claude" else None)  # Qwen Code not installed here
    assert "לא מותקן" in srv.action({"do": "engine_set", "engine": "qwen"})["error"] and srv.engine_info()["ready"]["qwen"] is False


def test_restart_requests_from_the_map(dash, monkeypatch):
    srv, root = dash
    assert srv.action({"do": "restart_agent"})["ok"] and os.path.exists(root / "agent" / "restart.request")  # the agent restarts after its answer
    seen = []
    monkeypatch.setattr(srv.subprocess, "run", lambda cmd, **k: seen.append(cmd) or None)
    assert srv.action({"do": "restart_bridge"})["ok"] and seen[0][:2] == ["taskkill", "/IM"] and "whatsapp-bridge.exe" in seen[0]


def test_the_map_machines_carry_the_engine_and_the_provider_models(dash, monkeypatch):
    srv, root = dash
    (root / "agent" / "providers.json").write_text(json.dumps({"groq": {"label": "Groq", "short": "Groq", "kind": "anthropic", "where": "remote", "key_file": "g.key"}}), encoding="utf-8")
    monkeypatch.setattr(srv, "provider_check_cached", lambda n, ttl=120: {"ok": True, "detail": "x", "models": ["m1", "m2"]})
    ai = srv.ai_map()["providers"][0]
    assert ai["models"] == ["m1", "m2"] and ai["id"] == "groq"
    b = srv.ai_brief()
    assert b["engine"]["engine"] == "claude" and "failover" in b["engine"]
