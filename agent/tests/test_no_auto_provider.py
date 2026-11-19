"""Owner 19.11 15:38: there is no "auto" provider; automatic choice exists only for the model (/מודל אוטו)."""
from test_agent import agent  # noqa: F401


def test_switch_to_auto_provider_refused(agent, monkeypatch):
    agent = agent[0]
    called = []
    monkeypatch.setattr(agent, "_switch_provider", lambda *a, **k: called.append(a) or "x")
    monkeypatch.setattr(agent, "active_provider", lambda chat=None: ("claude_work", "opus"))
    res = agent.switch_provider("auto", chat="g@g.us")
    assert not called
    assert "אין ספק אוטו" in res and "/מודל אוטו" in res


def test_commands_index_has_no_auto_provider_command(agent):
    agent = agent[0]
    assert "*/אוטו*" not in agent.commands_index()


def test_backup_computer_prefix_is_not_kept_in_the_name(agent):
    agent = agent[0]
    """Owner 19.11: after the living room (SL) held the bridge, the desktop's name still showed 'SL'."""
    assert "SL" not in agent.name_core("SL Claude עבודה YB אוטומציה 4M-D").split()
    assert "PR" not in agent.name_core("PR Claude עבודה YB אוטומציה 4M-D").split()
