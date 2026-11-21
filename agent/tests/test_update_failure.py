"""Owner 22.11: a failed auto-update is never undone by itself (no reset, no hard, never onto a tag); the reason goes to the
bridge-log group."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("update_t", os.path.join(ROOT, "tools", "update.py"))
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)


def _fake(monkeypatch, branch="main"):
    calls, told = [], []

    def git(*a, check=True):
        calls.append(a)
        if a[:1] == ("status",):
            return ""
        if a[:1] == ("symbolic-ref",):
            return branch
        return "abc1234"
    monkeypatch.setattr(up, "git", git)
    monkeypatch.setattr(up, "latest_release", lambda: {"version": "9.9.9", "tag": "v9.9.9", "assets": {}, "notes": ""})
    monkeypatch.setattr(up, "current_version", lambda: "1.2.3")
    for f in ("stop_services", "start_services", "sync_deps"):
        monkeypatch.setattr(up, f, lambda *a: None)
    monkeypatch.setattr(up, "new_bridge", lambda assets: None)
    monkeypatch.setattr(up, "update_dashboard", lambda assets: None)
    monkeypatch.setattr(up, "tell", lambda t: told.append(t))
    monkeypatch.setattr(up.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(up, "BRIDGE_EXE", os.path.join(ROOT, "no-such-dir", "whatsapp-bridge.exe"))
    up.FAILED.clear()
    return calls, told


def test_a_failed_update_is_told_and_never_undone(monkeypatch):
    calls, told = _fake(monkeypatch)
    monkeypatch.setattr(up.sys, "argv", ["update.py", "--simulate-fail"])
    assert up.main() == 2
    assert not any(c[0] in ("reset", "checkout") for c in calls), calls
    assert ("merge", "--ff-only", "--quiet", "v9.9.9") in calls, "stays on its branch, forward only"
    assert len(told) == 1 and "נכשל" in told[0] and "בדיקה מדומה" in told[0] and "לא בוצעה חזרה" in told[0]


def test_an_install_off_any_branch_is_never_moved_onto_a_tag(monkeypatch):
    calls, told = _fake(monkeypatch, branch="")
    monkeypatch.setattr(up.sys, "argv", ["update.py"])
    assert up.main() == 2
    assert not any(c[0] in ("reset", "checkout", "merge") for c in calls), calls
    assert "לא על ענף" in told[0]
