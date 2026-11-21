"""Owner 14.11: any hosted AI that connects to the bridge gets the conversation as context and the WhatsApp tools (read-only)."""
import json
import sqlite3

from test_agent import agent  # noqa: F401


def _fake_messages(mod, monkeypatch, rows):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT, is_from_me INTEGER)")
    db.execute("CREATE TABLE chats (jid TEXT, name TEXT)")
    db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", rows)
    db.executemany("INSERT INTO chats VALUES (?, ?)", [(chat, "קבוצת בדיקה") for chat in {r[1] for r in rows if r[1].endswith("@g.us")}])
    monkeypatch.setattr(mod, "mdb", lambda: db)


def test_a_fresh_session_gets_the_conversation_and_the_way_to_read_more(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "handoff_path", lambda chat: str(tmp_path / "handoff.md"))
    (tmp_path / "handoff.md").write_text("סיכום: דיברנו על המנוע החלופי", encoding="utf-8")
    c = mod.PRIMARY
    _fake_messages(mod, monkeypatch, [("1", c, "me", "מה המצב של הגשר?", "2026-11-14 08:01:00", 1), ("2", c, "me", "🤖 הגשר תקין, גרסה 1.1.1", "2026-11-14 08:01:30", 1),
                                      ("3", "other@g.us", "x", "הודעה מקבוצה אחרת", "2026-11-14 08:02:00", 0)])
    pack = mod.conversation_pack(c)
    assert "סיכום: דיברנו על המנוע החלופי" in pack and "[08:01] הבעלים: מה המצב של הגשר?" in pack and "[08:01] הבינה: הגשר תקין, גרסה 1.1.1" in pack  # owner 21.11: the memory holds no icons
    identity = mod.group_prompt(c)
    assert f'קבוצת בדיקה" (מזהה: {c}' in identity and "get_group_info" in identity and "set_group_name" in identity
    assert "הודעה מקבוצה אחרת" not in pack  # only this conversation
    assert "list_messages" in pack and "send_message" in pack and "אל תשלח הודעות" not in pack
    assert pack.index("מה המצב של הגשר?") < pack.index("הגשר תקין")  # oldest first


def test_qwen_settings_give_full_bridge_tools_and_keep_the_owners_own_keys(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod.os.path, "expanduser", lambda p: str(tmp_path / p.replace("~/", "")) if p.startswith("~") else p)
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    (tmp_path / "whatsapp-mcp-server" / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / "whatsapp-mcp-server" / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (tmp_path / ".qwen").mkdir()
    (tmp_path / ".qwen" / "settings.json").write_text(json.dumps({"ui": {"theme": "dark"}, "mcpServers": {"mine": {"command": "x"}}}), encoding="utf-8")
    assert mod.ensure_qwen_settings() is True
    cur = json.loads((tmp_path / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    assert cur["ui"] == {"theme": "dark"} and "mine" in cur["mcpServers"]  # what was there stays
    wa = cur["mcpServers"]["whatsapp"]
    assert wa["args"] == ["main.py"] and "includeTools" not in wa
    assert "includeTools" not in wa  # no MCP tool whitelist: the full bridge tool set is exposed
    assert cur["security"]["auth"]["selectedType"] == "openai" and cur["telemetry"]["enabled"] is False
    assert mod.ensure_qwen_settings() is False  # unchanged: nothing is rewritten


def test_the_gap_note_brings_claude_up_to_date_after_a_failover(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    c = mod.PRIMARY
    _fake_messages(mod, monkeypatch, [("1", c, "me", "שאלה בזמן המכסה", "2026-11-14 08:01:00", 1), ("2", c, "me", "🤖 תשובה מהבינה החלופית", "2026-11-14 08:02:00", 1)])
    seen = []
    monkeypatch.setattr(mod, "_run_auto", lambda prompt, *a, **k: seen.append(prompt) or {"result": "ok", "session_id": "s1"})
    monkeypatch.setattr(mod, "failover_target", lambda: None)
    mod.kv_set(mod.K("gap", c), "1")
    assert mod.claude("ההודעה החדשה", chat=c, on_stage=lambda *a: None) == "ok"
    assert "בינה או חשבון אחרים" in seen[0] and "תשובה מהבינה החלופית" in seen[0] and seen[0].rstrip().endswith("ההודעה החדשה")
    assert mod.kv_get(mod.K("gap", c)) == ""  # told once
    mod.claude("עוד הודעה", chat=c, on_stage=lambda *a: None)
    assert "בינה או חשבון אחרים" not in seen[1]


def test_standing_context_goes_once_per_session_then_only_what_changed(agent, monkeypatch):
    """Owner 20.11 10:55: the same rule for every provider: full context in a new session, later only a changed block."""
    mod, _ = agent
    store = {}
    monkeypatch.setattr(mod, "kv_get", lambda k: store.get(k, ""))
    monkeypatch.setattr(mod, "kv_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(mod, "providers", lambda: {"claude": {"kind": "claude"}, "gemini": {"kind": "cli"}, "app": {"kind": "desktop-app"}})
    c = "chat@g.us"
    blocks = {"asks": "A1", "learned": "L1"}
    send, hashes, tok = mod.ctx_select(c, "claude", None, blocks)
    assert send == blocks and tok is None  # no session yet: everything
    store[mod.K("ctx_tok", c)] = "claude:sid1"
    for n, h in hashes.items():
        store[mod.K("ctxh:" + n, c)] = h
    assert mod.ctx_select(c, "claude", "sid1", blocks)[0] == {}  # same session, nothing changed
    assert mod.ctx_select(c, "claude", "sid1", {"asks": "A2", "learned": "L1"})[0] == {"asks": "A2"}  # only the changed block
    assert mod.ctx_select(c, "claude", "sid2", blocks)[0] == blocks  # new session: everything again
    assert mod.ctx_select(c, "gemini", None, blocks)[0] == blocks  # a CLI without continuity always gets it
    assert mod.ctx_select(c, "auto", "sid1", blocks)[0] == blocks  # provider unknown: safe side
    store[mod.K("ctx_tok", c)] = "app:" + mod._ctx_token("app", None)
    assert mod.ctx_select(c, "app", None, blocks)[0] == {}  # a desktop app keeps its window session for the day


def _cli_state(mod, monkeypatch):
    store = {}
    monkeypatch.setattr(mod, "kv_get", lambda k: store.get(k, ""))
    monkeypatch.setattr(mod, "kv_set", lambda k, v: store.__setitem__(k, v))
    return store


def test_a_cli_continues_its_own_session_and_falls_back_to_a_fresh_one(agent, monkeypatch):
    """Owner 20.11 11:22: a CLI keeps its session between messages; a failed continued run is repeated once in a fresh session."""
    mod, _ = agent
    store = _cli_state(mod, monkeypatch)
    spec = {"id": "preset", "new": ["--session-id", "{id}"], "resume": ["--session-id", "{id}"]}
    p = {"kind": "cli", "cli": {"bin": "x", "resume": spec}}
    monkeypatch.setattr(mod, "providers", lambda: {"cp": p})
    seen = []

    def core(name, pp, prompt, model, append, chat, on_stage, timeout, sess=None):
        seen.append((sess["resume"], sess["id"]))
        if sess["resume"] and len(seen) == 2 and fail_resume[0]:
            raise RuntimeError("cp returned no answer")
        return {"result": "ok", "cli_session": None}
    fail_resume = [False]
    monkeypatch.setattr(mod, "_run_cli_core", core)
    c = "chat@g.us"
    mod._run_cli("cp", p, "a", None, "rules", c, None, 10)
    assert seen[0][0] is False and mod.cli_session_get("cp", c) == seen[0][1]  # new session, its id is kept
    mod._run_cli("cp", p, "b", None, "rules", c, None, 10)
    assert seen[1] == (True, seen[0][1])  # the next message continues the same session
    mod.cli_sessions_clear(c)
    assert mod.cli_session_get("cp", c) is None  # /סשן חדש starts over
    mod._run_cli("cp", p, "c", None, "rules", c, None, 10)
    fail_resume[0] = True
    seen.clear()
    mod._run_cli("cp", p, "d", None, "rules", c, None, 10)  # resume refused (call 1 is the resume: len(seen)==1 -> succeeds)
    assert seen[0][0] is True


def test_resume_failure_retries_fresh_once_and_switches_resume_off(agent, monkeypatch):
    mod, _ = agent
    store = _cli_state(mod, monkeypatch)
    spec = {"id": "preset", "new": ["--session-id", "{id}"], "resume": ["--resume", "{id}"]}
    p = {"kind": "cli", "cli": {"bin": "x", "resume": spec}}
    monkeypatch.setattr(mod, "providers", lambda: {"gm": p})
    c = "chat@g.us"
    store[mod.K("csession:gm", c)] = '{"id": "old", "date": "%s", "runs": 1}' % mod.dt.date.today().isoformat()
    calls = []

    def core(name, pp, prompt, model, append, chat, on_stage, timeout, sess=None):
        calls.append(sess["resume"])
        if sess["resume"]:
            raise RuntimeError("gm returned no answer")
        return {"result": "ok"}
    monkeypatch.setattr(mod, "_run_cli_core", core)
    assert mod._run_cli("gm", p, "x", None, "r", c, None, 10)["result"] == "ok"
    assert calls == [True, False] and store["cli_resume_off:gm"] == "1"
    assert mod.cli_session_get("gm", c) is None


def test_the_codex_session_id_comes_from_its_thread_event(agent, monkeypatch):
    mod, _ = agent
    ev = mod._codex_events('{"type":"thread.started","thread_id":"abc-1"}\n{"type":"turn.started"}')
    assert next(e["thread_id"] for e in ev if e["type"] == "thread.started") == "abc-1"
