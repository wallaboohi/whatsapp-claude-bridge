"""Owner 19.11: the 20-minute outage (17:38-17:58). A backup fenced the host and then did not take over, and the host waited
for backups that held its automations but not the WhatsApp identity. Nobody may be left without the connection."""
from test_agent import agent  # noqa: F401


def _host_env(mod, monkeypatch, granted):
    monkeypatch.setattr(mod, "chain_cfg", lambda: {"agent_grace_seconds": 30, "fence_seconds": 5, "net_check": False})
    monkeypatch.setattr(mod, "lease_held", lambda: False)
    monkeypatch.setattr(mod, "lease_replaced", lambda *a: False)
    monkeypatch.setattr(mod, "lease_grant", lambda why="": granted.append(why))
    monkeypatch.setattr(mod, "peer_fence", lambda *a: (_ for _ in ()).throw(AssertionError("no fence of an up peer")))
    mod._host_hold_seen.clear()


def test_host_connects_when_a_backup_holds_only_automations(agent, monkeypatch):
    mod, _ = agent
    granted = []
    _host_env(mod, monkeypatch, granted)
    monkeypatch.setattr(mod, "_alive_holds_lease", lambda d: False)
    view = {"חדר שינה": {"status": "up", "data": {"holds": ["נייח"]}}}
    mod.chain_host_step(view, now=1000.0)
    assert granted == [], "a take-over may be in progress: wait a little"
    mod.chain_host_step(view, now=1030.0)
    assert granted == []
    mod.chain_host_step(view, now=1061.0)
    assert granted == ["no PC in the chain holds the identity"], "after a minute WhatsApp is connected here again"


def test_host_never_connects_while_a_backup_holds_the_identity(agent, monkeypatch):
    mod, _ = agent
    granted = []
    _host_env(mod, monkeypatch, granted)
    monkeypatch.setattr(mod, "_alive_holds_lease", lambda d: bool(d.get("lease")))
    view = {"סלון": {"status": "up", "data": {"holds": ["נייח"], "lease": {"held": True}}}}
    for t in (1000.0, 1100.0, 5000.0):
        mod.chain_host_step(view, now=t)
    assert granted == [], "never two connections on one identity"


def test_a_fence_without_a_take_over_is_undone(agent, monkeypatch):
    mod, _ = agent
    answers = iter([("\"נייח\": הסוכן לא עונה 40 שנ' והמחשב כן", "נייח"), ("\"סלון\" מחזיק כבר את העבודה", None)])
    monkeypatch.setattr(mod, "chain_take_blocker", lambda *a, **k: next(answers))
    monkeypatch.setattr(mod, "chain_pass_marker", lambda host: "")
    monkeypatch.setattr(mod, "_fo_peer", lambda name: {"name": name, "ssh": "x"})
    monkeypatch.setattr(mod, "peer_fence", lambda peer, name: True)
    undone = []
    monkeypatch.setattr(mod, "peer_unfence", lambda peer, name, why="": undone.append(name) or True)
    monkeypatch.setattr(mod, "_fo_say", lambda *a, **k: None)
    mod._chain_backoff.clear()
    fc = {"for": "נייח", "self_name": "חדר שינה", "group": None, "defer_to": []}
    assert mod.chain_backup_step(fc, "נייח", {}, {"נייח": {"status": "agent_down", "since": 1}}) is False
    assert undone == ["נייח"], "the fenced host gets its grant back at once"


def test_unfence_writes_the_grant_on_the_peer(agent, monkeypatch):
    mod, _ = agent
    sent = {}
    monkeypatch.setattr(mod, "peer_alias", lambda p: "ybhw")
    monkeypatch.setattr(mod, "_remote_root", lambda p: "C:/Users/owner/whatsapp-mcp")
    monkeypatch.setattr(mod, "_remote_python", lambda peer, script, timeout=0: sent.update(script=script) or (0, "@@OK@@", ""))
    assert mod.peer_unfence({"name": "נייח"}, "נייח", "בדיקה") is True
    import base64, json, re
    cfg = json.loads(base64.b64decode(re.search(r"b64decode\('([^']+)'", sent["script"]).group(1)))
    assert '/lease_grant"' in sent["script"] and cfg["store"].endswith("whatsapp-bridge/store") and cfg["why"] == "בדיקה"


def test_a_push_is_held_while_committed_code_imports_an_untracked_module(agent, monkeypatch, tmp_path):
    mod, _ = agent
    import subprocess
    root = tmp_path / "repo"
    (root / "agent").mkdir(parents=True)
    (root / "agent" / "agent.py").write_text("import os\nimport asks\n", encoding="utf-8")
    (root / "agent" / "asks.py").write_text("X = 1\n", encoding="utf-8")
    run = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", "agent/agent.py")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "only agent.py")
    monkeypatch.setattr(mod, "ROOT", str(root))
    assert mod.missing_local_imports() == ["agent/asks.py"]
    run("add", "agent/asks.py")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "the module too")
    assert mod.missing_local_imports() == []


def test_the_unfence_script_runs_and_writes_one_line(agent, tmp_path):
    mod, _ = agent
    C = {"store": str(tmp_path).replace("\\", "/"), "who": "PC", "why": "x"}
    exec(compile(mod.UNFENCE, "unfence", "exec"), {"C": C})
    text = (tmp_path / "lease_grant").read_text(encoding="utf-8")
    assert text.startswith("PC ") and text.endswith("unfenced: x\n") and text.count("\n") == 1
