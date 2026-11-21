"""Owner 21.11 (the code guard): raise the agent / bridge again when down for any reason, keep uncommitted work, tag a good version,
and never restart into code that does not load."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("guard_watchdog", os.path.join(ROOT, "tools", "watchdog.py"))
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


def test_a_part_down_for_any_reason_is_raised_again_once_per_ten_minutes(monkeypatch):
    done = []
    monkeypatch.setattr(wd, "kill_agent", lambda: done.append("kill agent"))
    monkeypatch.setattr(wd, "kill", lambda image: done.append("kill " + image))
    monkeypatch.setattr(wd, "ensure_task", lambda name: done.append("task " + name) or "task was running")
    monkeypatch.setattr(wd, "notice", lambda kind, text: done.append("notice " + kind))
    st = {}
    assert wd.raise_again(st, "agent", 10_000).startswith("raised again")
    assert wd.raise_again(st, "agent", 10_000 + 60) == "raised a moment ago"
    assert wd.raise_again(st, "bridge", 10_000).startswith("raised again")
    assert done == ["kill agent", "task WhatsApp agent", "notice agent", "kill whatsapp-bridge.exe", "task WhatsApp bridge", "notice bridge"]


def test_a_version_healthy_for_an_hour_gets_a_good_tag(monkeypatch):
    tags = []
    monkeypatch.setattr(wd, "git", lambda *a: tags.append(a) or (0, ""))
    st = {}
    assert wd.tag_good(st, "abc", 0) == "" and wd.tag_good(st, "abc", 59 * 60) == ""
    t = wd.tag_good(st, "abc", 61 * 60)
    assert t.startswith("good-") and tags[-1][0] == "tag" and tags[-1][2] == "abc"
    assert wd.tag_good(st, "abc", 200 * 60) == "", "one tag per version"
    assert wd.tag_good(st, "def", 200 * 60) == "", "a new version starts its own hour"
