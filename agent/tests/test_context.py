"""Owner 14.11: any hosted AI that connects to the bridge gets the conversation as context and the WhatsApp tools (read-only)."""
import json
import sqlite3

from test_agent import agent  # noqa: F401


def _fake_messages(mod, monkeypatch, rows):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT, is_from_me INTEGER)")
    db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", rows)
    monkeypatch.setattr(mod, "mdb", lambda: db)


def test_a_fresh_session_gets_the_conversation_and_the_way_to_read_more(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "handoff_path", lambda chat: str(tmp_path / "handoff.md"))
    (tmp_path / "handoff.md").write_text("סיכום: דיברנו על המנוע החלופי", encoding="utf-8")
    c = mod.PRIMARY
    _fake_messages(mod, monkeypatch, [("1", c, "me", "מה המצב של הגשר?", "2026-11-14 08:01:00", 1), ("2", c, "me", "🤖 _הגשר תקין, גרסה 1.1.1_", "2026-11-14 08:01:30", 1),
                                      ("3", "other@g.us", "x", "הודעה מקבוצה אחרת", "2026-11-14 08:02:00", 0)])
    pack = mod.conversation_pack(c)
    assert "סיכום: דיברנו על המנוע החלופי" in pack and "[08:01] הבעלים: מה המצב של הגשר?" in pack and "[08:01] הבינה: 🤖" in pack
    assert "הודעה מקבוצה אחרת" not in pack  # only this conversation
    assert "list_messages" in pack and "אל תשלח הודעות" in pack
    assert pack.index("מה המצב של הגשר?") < pack.index("הגשר תקין")  # oldest first


def test_qwen_settings_give_read_only_whatsapp_tools_and_keep_the_owners_own_keys(agent, monkeypatch, tmp_path):
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
    assert wa["args"] == ["main.py"] and "send_message" not in wa["includeTools"] and "send_file" not in wa["includeTools"] and "react_to_message" not in wa["includeTools"]
    assert {"list_messages", "search_all", "get_message_context", "list_chats", "get_status"} <= set(wa["includeTools"])
    assert cur["security"]["auth"]["selectedType"] == "openai" and cur["telemetry"]["enabled"] is False
    assert mod.ensure_qwen_settings() is False  # unchanged: nothing is rewritten


def test_the_gap_note_brings_claude_up_to_date_after_a_failover(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))
    monkeypatch.setattr(mod, "ENGINE_FILE", str(tmp_path / "engine.json"))
    c = mod.PRIMARY
    _fake_messages(mod, monkeypatch, [("1", c, "me", "שאלה בזמן המכסה", "2026-11-14 08:01:00", 1), ("2", c, "me", "🤖 _תשובה מהבינה החלופית_", "2026-11-14 08:02:00", 1)])
    seen = []
    monkeypatch.setattr(mod, "_run_auto", lambda prompt, *a, **k: seen.append(prompt) or {"result": "ok", "session_id": "s1"})
    monkeypatch.setattr(mod, "failover_target", lambda: None)
    mod.kv_set(mod.K("gap", c), "1")
    assert mod.claude("ההודעה החדשה", chat=c, on_stage=lambda *a: None) == "ok"
    assert "בינה אחרת" in seen[0] and "תשובה מהבינה החלופית" in seen[0] and seen[0].rstrip().endswith("ההודעה החדשה")
    assert mod.kv_get(mod.K("gap", c)) == ""  # told once
    mod.claude("עוד הודעה", chat=c, on_stage=lambda *a: None)
    assert "בינה אחרת" not in seen[1]
