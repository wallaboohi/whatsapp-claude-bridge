"""Owner 20.11 11:57: the bridge never routes to a model the provider no longer has - every entry to a provider reads the
provider's own list, keeps the owner's order, adds new models at the end, and a dropped model is replaced before a run."""
import json
import os

from test_agent import agent  # noqa: F401


def test_sync_keeps_order_adds_new_drops_gone_and_replaces_a_gone_default(agent, tmp_path, monkeypatch):
    mod, _ = agent
    here = tmp_path / "agent"
    here.mkdir()
    prov = {"opencode": {"kind": "opencode", "model": "opencode/old-free",
                         "models": ["opencode/old-free", "opencode/keep-free"]},
            "claude": {"kind": "claude", "models": []}}
    (here / "providers.json").write_text(json.dumps(prov), encoding="utf-8")
    monkeypatch.setattr(mod, "PROVIDERS_FILE", str(here / "providers.json"))
    monkeypatch.setattr(mod, "CATALOG_FILE", str(here / "model_catalog.json"))
    monkeypatch.setattr(mod, "live_models", lambda name, force=False: ["opencode/keep-free", "opencode/new-free"])
    monkeypatch.setattr(mod, "MODELS_LIVE_FILE", str(here / "models_live.json"))
    before = (here / "providers.json").read_text(encoding="utf-8")
    res = mod.sync_models("opencode")
    assert res["removed"] == ["opencode/old-free"]
    assert res["added"] == ["opencode/new-free"]
    assert res["models"] == ["opencode/keep-free", "opencode/new-free"]  # the old order kept, the new model last
    # owner 20.11: the live list is this PC's own (models_live.json); the shared providers.json in git is never rewritten
    assert (here / "providers.json").read_text(encoding="utf-8") == before
    live = mod.providers()["opencode"]
    assert live["models"] == ["opencode/keep-free", "opencode/new-free"]
    assert live["model"] == "opencode/keep-free"  # a default model that disappeared is replaced


def test_nothing_changes_when_the_provider_still_has_the_same_models(agent, tmp_path, monkeypatch):
    mod, _ = agent
    here = tmp_path / "agent"
    here.mkdir()
    prov = {"opencode": {"kind": "opencode", "model": "opencode/a-free", "models": ["opencode/a-free"]}}
    (here / "providers.json").write_text(json.dumps(prov), encoding="utf-8")
    before = os.path.getmtime(here / "providers.json")
    monkeypatch.setattr(mod, "PROVIDERS_FILE", str(here / "providers.json"))
    monkeypatch.setattr(mod, "MODELS_LIVE_FILE", str(here / "models_live.json"))
    monkeypatch.setattr(mod, "live_models", lambda name, force=False: ["opencode/a-free"])
    assert mod.sync_models("opencode") == {"added": [], "removed": [], "models": ["opencode/a-free"]}
    assert os.path.getmtime(here / "providers.json") == before  # no needless rewrite (no git churn between the computers)
    assert not (here / "models_live.json").exists()


def test_a_model_the_provider_dropped_is_never_sent(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "live_models", lambda name, force=False: ["opencode/a-free", "opencode/b-free"])
    assert mod.live_valid_model("opencode", "opencode/gone-free") == "opencode/a-free"
    assert mod.live_valid_model("opencode", "opencode/b-free") == "opencode/b-free"
    # a provider that brings its own model or has no live list: the model is left exactly as it was
    monkeypatch.setattr(mod, "live_models", lambda name, force=False: [])
    assert mod.live_valid_model("codex", "whatever") == "whatever"


def test_only_a_whole_catalog_provider_gains_new_models(agent, tmp_path, monkeypatch):
    """Groq/Gemini also list speech and TTS models in their own catalog: there a gone model is dropped, a new one is not added."""
    mod, _ = agent
    here = tmp_path / "agent"
    here.mkdir()
    prov = {"groq": {"kind": "anthropic", "model": "a/one", "models": ["a/one", "a/gone"]}}
    (here / "providers.json").write_text(json.dumps(prov), encoding="utf-8")
    monkeypatch.setattr(mod, "PROVIDERS_FILE", str(here / "providers.json"))
    monkeypatch.setattr(mod, "live_models", lambda name, force=False: ["a/one", "whisper-large-v3"])
    res = mod.sync_models("groq")
    assert res["models"] == ["a/one"] and res["removed"] == ["a/gone"] and res["added"] == []


def test_a_dropped_model_answer_is_recognised_in_hebrew_free_text(agent):
    mod, _ = agent
    for text in ("Model unavailable: opencode/minimax-m3.1-flash-free", "404 model not found",
                 "unknown model 'x'", "The model `y` does not exist"):
        assert mod.MODEL_GONE_RE.search(text), text
    assert not mod.MODEL_GONE_RE.search("הנה סיכום של המודלים שדיברנו עליהם אתמול")
