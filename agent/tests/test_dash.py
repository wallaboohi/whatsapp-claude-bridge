"""Dashboard backend tests: a temporary tool root with its own databases, no bridge, no agent."""
import importlib
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
PRIM, DEV = "1203630000@g.us", "1203639999@g.us"


@pytest.fixture()
def dash(tmp_path, monkeypatch):
    (tmp_path / "agent" / "home").mkdir(parents=True)
    (tmp_path / "agent" / "group_templates").mkdir()
    (tmp_path / "agent" / "group_templates" / "dev.md").write_text("הוראות {owner}", encoding="utf-8")
    (tmp_path / "agent" / "models.json").write_text("{}", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    m = sqlite3.connect(store / "messages.db")
    m.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT, filename TEXT, PRIMARY KEY (id, chat_jid))")
    m.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    m.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, 0, '', '')", [
        ("a", "972500000001@s.whatsapp.net", "972500000001", "צריך עוד קסדות מידה L לצוות", "2026-11-07 09:00:00+02:00"),
        ("b", PRIM, "me", "🤖 _קיבלתי_", "2026-11-07 09:01:00+02:00"),
    ])
    m.commit(); m.close()
    cfg = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    cfg.update({"store_dir": str(store), "claude_group": PRIM, "plugins": []})
    (tmp_path / "agent" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("WA_TOOL_ROOT", str(tmp_path))
    sys.path.insert(0, ROOT)
    import dash.server as srv
    srv = importlib.reload(srv)
    monkeypatch.setattr(srv, "bridge", lambda path, body=None, timeout=8: {"members": 1, "name": "x"} if path == "/group_info" else {})
    monkeypatch.setattr(srv, "services", lambda force=False: {"bridge_loop": True, "agent_loop": True, "bridge_proc": True, "agent_proc": True})
    return srv, tmp_path


def test_say_pc_queues_a_command_and_shows_it(dash):
    srv, root = dash
    r = srv.action({"do": "say_pc", "chat": "local", "text": "שלום", "sync": 0})
    assert r["ok"]
    con = sqlite3.connect(root / "agent" / "agent.db")
    assert con.execute("SELECT chat_jid, source, sync, text FROM commands").fetchall() == [("local", "pc", 0, "שלום")]
    conv = srv.conversation_of("local", srv.cfg(), srv.paths())
    assert [(m["content"], m["tool"], m["src"]) for m in conv] == [("שלום", False, "pc")]


def test_connect_group_creates_instructions_and_policy(dash):
    srv, root = dash
    pol = {}
    srv.bridge = lambda path, body=None, timeout=8: pol.update(body or {}) or ({"members": 1} if path == "/group_info" else {})
    r = srv.action({"do": "group_connect", "jid": DEV, "name": "פיתוח", "purpose": "dev"})
    assert r["ok"]
    assert "הוראות" in open(root / "agent" / "home" / "groups" / "1203639999.md", encoding="utf-8").read()
    assert DEV in srv.conversations() and pol["control_groups"] == [DEV]
    assert srv.model_info(DEV)["current"] == "opus" and srv.model_info(PRIM)["current"] == "auto"
    assert srv.action({"do": "group_disconnect", "jid": DEV})["ok"] and DEV not in srv.conversations()
    assert srv.action({"do": "group_disconnect", "jid": PRIM})["error"]


def test_group_with_other_members_needs_confirmation(dash):
    srv, _ = dash
    srv.bridge = lambda path, body=None, timeout=8: {"members": 5}
    assert srv.action({"do": "group_connect", "jid": DEV, "name": "x", "purpose": "general"}) == {"needs_confirm": True, "members": 5}
    assert srv.action({"do": "group_connect", "jid": DEV, "name": "x", "purpose": "general", "confirm_members": True})["ok"]


def test_search_finds_typos_and_short_queries(dash):
    srv, _ = dash
    assert srv.search("קסדות")["results"][0]["snippet"].find("קסדות") >= 0
    assert srv.search("קסדאת")["results"], "one wrong letter must still find it"
    assert srv.search("מי")["results"] or True  # 2-letter queries use LIKE and must not crash
    assert srv.search("zzzzqqq")["results"] == []
    assert srv.search("קסדות")["results"][0]["chat"] == "972500000001@s.whatsapp.net"


def test_budget_can_be_set_per_group(dash):
    srv, _ = dash
    srv.action({"do": "group_connect", "jid": DEV, "name": "x", "purpose": "general"})
    assert srv.action({"do": "group_budget", "jid": DEV, "budget": "12.5"})["ok"]
    assert srv.conversations()[DEV]["budget_usd"] == 12.5
    assert srv.action({"do": "group_budget", "jid": DEV, "budget": "-1"})["error"]



def _agent_db(root):
    return sqlite3.connect(root / "agent" / "agent.db")


def test_listening_pause_and_resume(dash):
    srv, root = dash
    assert srv.action({"do": "listen_pause"})["ok"] and srv.kv("listen_paused")
    assert srv.action({"do": "listen_pause", "chat": DEV})["ok"] and srv.kv("listen_paused:" + DEV)
    srv.action({"do": "listen_resume"})
    srv.action({"do": "listen_resume", "chat": DEV})
    assert not srv.kv("listen_paused") and not srv.kv("listen_paused:" + DEV)
    assert srv.data()["listen_paused"] == ""


def test_queue_cancel_retry_clear(dash):
    srv, root = dash
    srv.agent_db_rw().close()
    con = _agent_db(root)
    con.executemany("INSERT INTO commands (msg_id, text, at, status, chat_jid) VALUES (?, ?, '2026-11-08 10:00', ?, ?)",
                    [("a", "x", "pending", PRIM), ("b", "y", "interrupted", PRIM), ("c", "z", "running", PRIM), ("d", "w", "waiting_limit", DEV)])
    con.commit(); con.close()
    q = {r["id"]: r for r in srv.data()["queue"]}
    assert {r["status"] for r in q.values()} == {"pending", "interrupted", "running", "waiting_limit"}
    assert srv.action({"do": "cmd_retry", "id": 2})["ok"]           # interrupted -> pending
    assert srv.action({"do": "cmd_cancel", "id": 3}).get("error")   # running: use stop
    assert srv.action({"do": "cmd_cancel", "id": 1})["ok"]
    assert srv.action({"do": "cmd_clear", "chat": DEV})["cancelled"] == 1
    st = dict(_agent_db(root).execute("SELECT id, status FROM commands").fetchall())
    assert st == {1: "cancelled", 2: "pending", 3: "running", 4: "cancelled"}


def test_scheduled_and_reminders(dash):
    srv, root = dash
    srv.agent_db_rw().close()
    con = _agent_db(root)
    con.execute("CREATE TABLE IF NOT EXISTS scheduled_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER, send_at TEXT, status TEXT DEFAULT 'scheduled', chat TEXT, created TEXT, done TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT DEFAULT 'time', text TEXT, due TEXT, chat_jid TEXT, ref_ts TEXT, status TEXT DEFAULT 'active', created TEXT, fired TEXT)")
    con.execute("INSERT INTO scheduled_drafts (draft_id, send_at) VALUES (7, '2026-11-09T08:00:00')")
    con.executemany("INSERT INTO reminders (text, due) VALUES (?, '2026-11-09T08:00:00+02:00')", [("a",), ("b",)])
    con.commit(); con.close()
    d = srv.data()
    assert len(d["scheduled"]) == 1 and len(d["reminders"]) == 2
    assert srv.action({"do": "sched_cancel", "id": 1})["ok"] and srv.data()["scheduled"] == []
    assert srv.action({"do": "rem_cancel", "id": 1})["ok"]
    assert srv.action({"do": "rem_snooze", "id": 2, "minutes": 30})["ok"]
    assert [r["id"] for r in srv.data()["reminders"]] == [2]
    assert srv.action({"do": "rem_snooze", "id": 2, "minutes": 0}).get("error")


def test_settings_validated_and_saved_atomically(dash):
    srv, root = dash
    r = srv.action({"do": "settings_save", "values": {"backup_time": "25:00"}})
    assert r.get("error")
    r = srv.action({"do": "settings_save", "values": {"backup_time": "02:30", "voice_replies": False, "daily_budget_usd": "15",
                                                      "permission_mode": "bypassPermissions", "unknown_key": 1}, "restart": True})
    assert r["ok"]
    c = json.load(open(root / "agent" / "config.json", encoding="utf-8"))
    assert c["backup_time"] == "02:30" and c["voice_replies"] is False and c["daily_budget_usd"] == 15.0
    assert c["permission_mode"] == "bypassPermissions" and "unknown_key" not in c
    assert (root / "agent" / "restart.request").exists()
    assert srv.action({"do": "settings_save", "values": {"permission_mode": "everything"}}).get("error")
    v = srv.data()["settings"]["values"]
    assert v["backup_time"] == "02:30" and v["total_daily_budget_usd"] == 30.0


def test_plugins_toggle_and_disabled_groups(dash):
    srv, root = dash
    c = srv.cfg(); c["plugins"] = ["C:/x/p1.py"]; srv.save_cfg(c)
    assert srv.action({"do": "plugin_toggle", "path": "C:/x/p1.py", "on": False})["ok"]
    c = srv.cfg()
    assert c["plugins"] == [] and c["plugins_disabled"] == ["C:/x/p1.py"]
    assert srv.action({"do": "plugin_toggle", "path": "C:/x/p1.py", "on": True})["ok"] and srv.cfg()["plugins"] == ["C:/x/p1.py"]
    srv.save_groups_raw({DEV: {"name": "dev", "enabled": False}})
    assert srv.data()["disabled_groups"] == [{"jid": DEV, "name": "dev"}]
    assert srv.action({"do": "group_enable", "jid": DEV, "on": True})["ok"] and DEV in srv.conversations()


def test_session_new_is_queued_for_the_agent(dash):
    srv, root = dash
    assert srv.action({"do": "session_new", "chat": "local"})["ok"]
    assert _agent_db(root).execute("SELECT text, chat_jid, source FROM commands").fetchall() == [("/סשן חדש", "local", "pc")]
    assert srv.action({"do": "session_new", "chat": "nope@g.us"}).get("error")


def test_peers_save_validate_and_offline_proxy(dash):
    srv, root = dash
    assert srv.action({"do": "peers_save", "peers": [{"name": "x", "url": "http://evil.com:80"}]}).get("error")
    assert srv.action({"do": "peers_save", "peers": [{"name": "נייח", "url": "http://127.0.0.1:59999", "tunnel": "ybhw"}]})["ok"]
    assert srv.peers() == [{"name": "נייח", "url": "http://127.0.0.1:59999", "tunnel": "ybhw"}]
    r = srv.peer_call(0, "/brief", timeout=1)
    assert r["offline"]
    st = srv.peers_status()
    assert st["self"]["groups"] and st["peers"][0]["online"] is False
    assert srv.peer_call(5, "/brief")["offline"]  # unknown peer index
    assert "already running" in srv.ssh_start_script("C:/t") and "run-agent.cmd" in srv.ssh_start_script("C:/t")


def test_tunnel_switch_and_service_validation(dash):
    srv, root = dash
    assert srv.action({"do": "tunnel", "name": "נייח", "on": False})["ok"] and srv.kv("tunnel_off:נייח") == "1"
    assert srv.action({"do": "tunnel", "name": "נייח", "on": True})["ok"] and srv.kv("tunnel_off:נייח") == ""
    assert srv.action({"do": "service", "what": "everything", "op": "stop"}).get("error")



def test_settings_transcribe_off_records_gap_and_backfill(dash):
    srv, tmp = dash
    srv.action({"do": "settings_save", "values": {"transcribe": False}})
    assert srv.kv("transcribe_off_since"), "switching transcription off remembers since when"
    assert srv.action({"do": "transcribe_backfill"}).get("error"), "can't fill the gap while it is off"
    srv.action({"do": "settings_save", "values": {"transcribe": True, "host_label": "", "claude_account": "עבודה"}})
    assert srv.action({"do": "transcribe_backfill"}) == {"ok": True} and srv.kv("transcribe_backfill_request")
    g = srv.transcribe_gap_info()
    assert g["on"] and g["explicit"] and "missing" in g
    assert srv.cost_summary()["account"] == "עבודה"


def test_history_sync_and_peer_ssh_field(dash, monkeypatch):
    srv, tmp = dash
    calls = []
    monkeypatch.setattr(srv, "bridge", lambda path, body=None, timeout=8: calls.append((path, body)) or {"requested": True})
    assert srv.action({"do": "history_sync", "jid": "bad"}).get("error")
    r = srv.action({"do": "history_sync", "jid": "972500000001@s.whatsapp.net", "count": 9999})
    assert r.get("ok") and calls[-1] == ("/history_sync", {"jid": "972500000001@s.whatsapp.net", "count": 500})
    assert srv.action({"do": "peers_save", "peers": [{"name": "לפטופ", "url": "http://127.0.0.1:8781", "ssh": "4-MINE"}]}) == {"ok": True}
    assert srv.peers()[0]["ssh"] == "4-MINE" and srv.peer_alias(srv.peers()[0]) == "4-MINE"
    assert srv.action({"do": "peers_save", "peers": [{"name": "x", "url": "http://127.0.0.1:8781", "ssh": "bad alias;rm"}]}).get("error")


def test_provider_add_from_the_legend_keeps_the_key_out_of_the_json(dash, monkeypatch):
    srv, root = dash
    (root / "agent" / "providers.json").write_text(json.dumps({"claude": {"label": "Claude", "kind": "claude"}}), encoding="utf-8")
    monkeypatch.setattr(srv, "anthropic_probe", lambda p, tok: {"ok": True, "detail": "מחובר", "models": list(p["models"])})
    bad = srv.action({"do": "provider_add", "id": "deepseek", "base_url": "http://evil.example", "model": "m"})
    assert bad.get("error")  # plain http to a remote host is refused
    assert srv.action({"do": "provider_add", "id": "x", "base_url": "https://a.b", "model": "m"}).get("error")  # id too short
    r = srv.action({"do": "provider_add", "id": "DeepSeek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic/",
                    "model": "deepseek-chat", "models": "deepseek-chat, deepseek-reasoner", "key": "sk-abc123"})
    assert r["ok"] and r["id"] == "deepseek" and r["check"]["ok"]
    saved = json.loads((root / "agent" / "providers.json").read_text(encoding="utf-8"))
    assert saved["claude"]["kind"] == "claude" and saved["deepseek"]["base_url"] == "https://api.deepseek.com/anthropic"
    assert saved["deepseek"]["models"] == ["deepseek-chat", "deepseek-reasoner"] and saved["deepseek"]["key_file"] == "deepseek.key"
    assert "sk-abc123" not in json.dumps(saved)  # the key lives only in secrets/
    assert (root / "secrets" / "deepseek.key").read_text(encoding="utf-8") == "sk-abc123"
    assert srv.action({"do": "provider_add", "id": "deepseek", "base_url": "https://a.b", "model": "m"}).get("error")  # no overwrite
    assert "sk-abc123" not in json.dumps(srv.legend())


def test_anthropic_probe_explains_a_rejected_key(dash, monkeypatch):
    srv, _ = dash
    import io, urllib.error

    def refuse(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(srv.urllib.request, "urlopen", refuse)
    r = srv.anthropic_probe({"base_url": "https://x.y/anthropic", "models": ["m1"]}, "tok")
    assert not r["ok"] and "המפתח נדחה" in r["detail"] and r["models"] == ["m1"]
