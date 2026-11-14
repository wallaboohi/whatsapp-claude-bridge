"""Owner 14.11: which Claude account and which AI run on which computer, and changing them from the map / WhatsApp."""
import io
import json

from test_agent import agent  # noqa: F401
from test_dash import dash  # noqa: F401


class _P:
    returncode = 0

    def __init__(self, out):
        self.stdout = out


def test_dash_reports_the_claude_account_and_the_ai_of_this_computer(dash, monkeypatch):
    srv, root = dash
    (root / "agent" / "providers.json").write_text(json.dumps({"claude": {"label": "Claude", "short": "Claude", "kind": "claude", "where": "remote"}}), encoding="utf-8")
    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: _P(json.dumps({"loggedIn": True, "email": "a@b.com", "orgName": "Org", "subscriptionType": "team"})))
    srv._CLAUDE_ACC.update(at=0.0, v=None)
    assert srv.claude_account_info() == {"logged_in": True, "email": "a@b.com", "org": "Org", "plan": "team"}
    ai = srv.ai_brief()
    assert ai["provider"] == "claude" and ai["where"] == "remote" and ai["claude"]["email"] == "a@b.com"
    assert "ai" in srv.brief() and srv.brief()["ai"]["claude"]["plan"] == "team"
    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no claude")))
    assert srv.claude_account_info(force=True)["logged_in"] is False  # never an exception: "not signed in / unknown"


def test_changing_the_claude_account_from_the_map_link_then_code(dash, monkeypatch):
    srv, _ = dash

    class FakeProc:
        def __init__(self, *a, **k):
            self.stdin = io.StringIO()
            self.stdout = iter(["Opening browser to sign in…\n", "visit: https://claude.com/cai/oauth/authorize?code=true&state=zz\n", "Paste code here >\n"])
            self._done = False

        def poll(self):
            return 0 if self._done else None

        def wait(self, timeout=None):
            self._done = True

        def kill(self):
            self._done = True
    made = []
    monkeypatch.setattr(srv.subprocess, "Popen", lambda *a, **k: made.append(FakeProc()) or made[-1])
    r = srv.action({"do": "claude_login_start"})
    assert r["url"].startswith("https://claude.com/cai/oauth/authorize") and srv.action({"do": "claude_login_start"})["url"] == r["url"]  # one open login, not two
    assert "לא תקין" in srv.action({"do": "claude_login_code", "code": "x"})["error"]
    monkeypatch.setattr(srv.subprocess, "run", lambda *a, **k: _P(json.dumps({"loggedIn": True, "email": "new@acct.com", "orgName": "", "subscriptionType": "pro"})))
    done = srv.action({"do": "claude_login_code", "code": "ABCDEFGHIJKLMN"})
    assert done["ok"] and done["account"]["email"] == "new@acct.com"
    assert made[0].stdin.getvalue() == "ABCDEFGHIJKLMN\n" and "claude_login_code" and srv._LOGIN["proc"] is None
    assert "אין התחברות פתוחה" in srv.action({"do": "claude_login_code", "code": "ABCDEFGHIJKLMN"})["error"]


def test_whatsapp_accounts_command_lists_every_computer(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "claude_account", lambda max_age=3600: "wallaboohi@gmail.com (Pro)")
    monkeypatch.setitem(mod.CFG, "peers", [{"name": "4-MINE", "host_label": "לפטופ"}])
    monkeypatch.setattr(mod, "peer_fetch", lambda p, timeout=10: {"machine": "lap-yakir", "host_label": "לפטופ", "ai": {
        "provider": "claude", "short": "Claude", "model": "opus", "where": "remote", "claude": {"email": "yakirb@4-mine.com", "plan": "team", "org": "4M Defense"}}})
    text = mod.owner_command("/חשבונות", mod.PRIMARY)
    assert "wallaboohi@gmail.com (Pro)" in text and "yakirb@4-mine.com (team, 4M Defense)" in text and "☁️" in text and "לפטופ" in text
    monkeypatch.setattr(mod, "peer_fetch", lambda p, timeout=10: None)
    assert "לא זמין" in mod.owner_command("/חשבון", mod.PRIMARY)
