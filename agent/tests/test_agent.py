"""Agent tests that need no WhatsApp account: a temporary store + agent database."""
import importlib
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    db = sqlite3.connect(store / "messages.db")
    db.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN, "
               "media_type TEXT, filename TEXT, PRIMARY KEY (id, chat_jid))")
    db.commit()
    cfg = json.load(open(os.path.join(HERE, "config.example.json"), encoding="utf-8"))
    cfg.update({"store_dir": str(store), "claude_group": "1203630000@g.us", "transcribe": False, "plugins": []})
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("WA_AGENT_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("WA_AGENT_DB", str(tmp_path / "agent.db"))
    sys.path.insert(0, HERE)
    mod = importlib.reload(importlib.import_module("agent"))
    mod.LOG = str(tmp_path / "agent.log")
    # never touch the real installation's files from a test (model choice, groups, restart / stop requests)
    for name, fname in (("MODEL_FILE", "model_override.json"), ("GROUPS_FILE", "groups.json"),
                        ("RESTART_FILE", "restart.request"), ("STOP_FILE", "stop.request")):
        monkeypatch.setattr(mod, name, str(tmp_path / fname))
    mod.adb().close()
    return mod, db


def add(db, mid, ts, content="hi", chat="972500000001@s.whatsapp.net"):
    db.execute("INSERT INTO messages VALUES (?, ?, 'x', ?, ?, 0, '', '')", (mid, chat, content, ts))
    db.commit()


def test_every_message_handled_exactly_once(agent):
    mod, db = agent
    seen = []
    add(db, "A", "2026-11-06 10:00:00+02:00")
    add(db, "B", "2026-11-06 10:01:00+02:00")
    cur = mod.sweep_once(0, seen.append)
    cur = mod.sweep_once(cur, seen.append)
    assert [r[0] for r in seen] == ["A", "B"]
    # a message stored later but carrying an OLDER timestamp (offline sync) must still be picked up
    add(db, "C", "2026-11-06 09:00:00+02:00")
    mod.sweep_once(cur, seen.append)
    assert [r[0] for r in seen] == ["A", "B", "C"]
    # a replaced row (new rowid) is not handled again
    db.execute("INSERT OR REPLACE INTO messages VALUES ('A', '972500000001@s.whatsapp.net', 'x', 'hi', '2026-11-06 10:00:00+02:00', 0, '', '')")
    db.commit()
    mod.sweep_once(0, seen.append)
    assert [r[0] for r in seen] == ["A", "B", "C"]


def test_owner_commands_are_durable(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "react", lambda *a: None)
    mod.enqueue_command("M1", "hello", "2026-11-06 10:00")
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE msg_id = 'M1'").fetchone()[0] == "pending"


def test_strings_exist_in_both_languages(agent):
    mod, _ = agent
    src = open(os.path.join(HERE, "agent.py"), encoding="utf-8").read()
    he = src[src.index('"he": {'):src.index('"en": {')]
    en = src[src.index('"en": {'):src.index("}[LANG]")]
    keys = lambda block: {line.split('"')[1] for line in block.splitlines() if line.strip().startswith('"') and '":' in line}
    assert keys(he) - {"he"} == keys(en) - {"en"}


def test_plugin_hooks_register(agent, tmp_path):
    mod, _ = agent
    plugin = tmp_path / "p.py"
    plugin.write_text("def setup(a):\n    a.on_message(lambda row: None)\n    a.every(5, 'k', lambda: None)\n    a.manual('job', lambda: None)\n")
    mod.CFG["plugins"] = [str(plugin)]
    before = (len(mod._hooks), len(mod._periodic))
    mod.load_plugins()
    assert (len(mod._hooks), len(mod._periodic)) == (before[0] + 1, before[1] + 1)
    assert "job" in mod._manual


def test_italic_marks_tool_messages(agent):
    mod, _ = agent
    assert mod.italic("שלום\n\n  • נקודה ") == "_שלום_\n\n  _• נקודה_"
    assert mod.italic("https://x.com/a_b") == "https://x.com/a_b"  # underscores: left as is
    assert mod.italic("```code```") == "```code```"


def test_run_cost_from_cumulative_session_figure(agent):
    mod, _ = agent
    assert mod.run_cost(0.4, None) == 0.4             # first run of a session
    assert round(mod.run_cost(8.65, 8.63), 2) == 0.02  # resumed session: only this run's share
    assert mod.run_cost(0.3, 8.0) == 0.3               # lower figure = a new count


def test_old_cumulative_rows_are_converted(agent, tmp_path):
    mod, _ = agent
    with mod.adb() as db:
        db.execute("UPDATE claude_runs SET cum = NULL")
        for at, cost in (("2026-11-07T10:00", 5.0), ("2026-11-07T10:05", 5.5), ("2026-11-07T10:10", 6.0)):
            db.execute("INSERT INTO claude_runs (at, session, cost, cum) VALUES (?, 's1', ?, NULL)", (at, cost))
    mod._runs_migrated = False
    with mod.adb() as db:
        costs = [round(r[0], 2) for r in db.execute("SELECT cost FROM claude_runs WHERE session = 's1' ORDER BY at")]
    assert costs == [5.0, 0.5, 0.5]


def test_restart_command_and_strong_model_prefix(agent):
    mod, _ = agent
    assert mod.owner_command("/הפעל מחדש") and mod.RESTART_REQ.is_set()
    mod.RESTART_REQ.clear()
    mod.CFG["deep_model"] = "opus"
    assert mod.pick_model("!! תבדוק לעומק") == "opus"
    assert mod.pick_model("!!! מה המצב") != "opus" or mod.get_override() == "opus"


# ---------- several conversations: groups, local chat, per-conversation session / model / budget ----------
DEV = "1203639999@g.us"


@pytest.fixture()
def multi(agent, tmp_path, monkeypatch):
    mod, db = agent
    gf = tmp_path / "groups.json"
    gf.write_text(json.dumps({DEV: {"name": "פיתוח", "purpose": "dev", "slug": "dev", "budget_usd": 7, "enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(mod, "GROUPS_FILE", str(gf))
    monkeypatch.setattr(mod, "MODEL_FILE", str(tmp_path / "model_override.json"))
    return mod, db


def test_groups_registry_and_keys(multi):
    mod, _ = multi
    g = mod.load_groups()
    assert list(g) == [mod.PRIMARY, DEV, mod.LOCAL]
    assert mod.K("session_id", mod.PRIMARY) == "session_id"          # the primary group keeps its legacy keys
    assert mod.K("session_id", DEV) == f"session_id:{DEV}" and mod.K("session_id") == "session_id"
    assert mod.budget_for(DEV) == 7 and mod.budget_for(mod.PRIMARY) == float(mod.CFG.get("daily_budget_usd", 5))


def test_model_choice_is_independent_per_conversation(multi):
    mod, _ = multi
    mod.set_override("opus", None, DEV)
    assert mod.get_override(DEV) == "opus" and mod.get_override(mod.PRIMARY) is None and mod.get_override(mod.LOCAL) is None
    mod.set_override("haiku", None, mod.PRIMARY)
    mod.set_override("sonnet", None, mod.LOCAL)
    assert (mod.get_override(mod.PRIMARY), mod.get_override(DEV), mod.get_override(mod.LOCAL)) == ("haiku", "opus", "sonnet")
    assert mod.pick_model("שלום", DEV) == "opus"
    mod.set_override("auto", None, DEV)
    assert mod.get_override(DEV) is None and mod.get_override(mod.PRIMARY) == "haiku"


def test_spend_is_counted_per_conversation(multi):
    mod, _ = multi
    now = mod.dt.datetime.now().isoformat()
    with mod.adb() as db:
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 1.0, NULL)", (now,))      # old row = primary
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 2.0, ?)", (now, mod.PRIMARY))
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 4.0, ?)", (now, DEV))
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 8.0, ?)", (now, mod.LOCAL))
    assert (mod.spent_today(mod.PRIMARY), mod.spent_today(DEV), mod.spent_today(mod.LOCAL)) == (3.0, 4.0, 8.0)
    assert mod.spent_today() == 15.0


def _run_worker(mod, monkeypatch, answer="התשובה"):
    """Start the command worker with Claude and the bridge faked; returns the list of (chat, text) sent to WhatsApp and the claude calls."""
    sent, calls = [], []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: {})
    monkeypatch.setattr(mod, "claude", lambda prompt, model=None, session=True, timeout=900, on_stage=None, chat=None, **k: calls.append((chat, model, prompt)) or answer)
    import threading
    threading.Thread(target=mod.command_worker, daemon=True).start()
    return sent, calls


def _wait_done(mod, n=1, secs=10):
    import time
    end = time.time() + secs
    while time.time() < end:
        with mod.adb() as db:
            if db.execute("SELECT count(*) FROM commands WHERE status = 'done'").fetchone()[0] >= n:
                return True
        time.sleep(0.2)
    return False


def test_pc_message_is_answered_in_the_dashboard_only(multi, monkeypatch):
    mod, _ = multi
    sent, calls = _run_worker(mod, monkeypatch)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('pc-1', 'שלום מהמחשב', '2026-11-07 22:00', ?, 'pc', 0)", (DEV,))
    assert _wait_done(mod)
    assert not [x for x in sent if "התשובה" in x[1]], "a PC-only answer must not be posted to WhatsApp"
    with mod.adb() as db:
        rows = db.execute("SELECT chat, role, text FROM local_messages").fetchall()
    assert (DEV, "tool", "התשובה") in rows
    assert calls[0][0] == DEV and "מהמחשב" in calls[0][2]


def test_pc_message_with_sync_also_goes_to_whatsapp(multi, monkeypatch):
    mod, _ = multi
    sent, _ = _run_worker(mod, monkeypatch)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('pc-2', 'עם סנכרון', '2026-11-07 22:00', ?, 'pc', 1)", (DEV,))
    assert _wait_done(mod)
    assert any(c == DEV and "התשובה" in t for c, t in sent)


def test_local_chat_never_touches_whatsapp(multi, monkeypatch):
    mod, _ = multi
    sent, calls = _run_worker(mod, monkeypatch)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('pc-3', 'שאלה מקומית', '2026-11-07 22:00', 'local', 'pc', 1)")
    assert _wait_done(mod)
    assert sent == [], "the local chat has no WhatsApp group, even with sync set"
    assert calls[0][0] == "local"


def test_group_message_is_answered_in_that_group(multi, monkeypatch):
    mod, db = multi
    sent, calls = _run_worker(mod, monkeypatch)
    monkeypatch.setattr(mod, "react", lambda *a, **k: None)
    mod.handle(("m1", DEV, "me", "2026-11-07 22:10:00+02:00", "מה המצב בגשר?", True, ""))
    assert _wait_done(mod)
    assert any(c == DEV and "התשובה" in t for c, t in sent) and calls[0][0] == DEV
    assert not any(c == mod.PRIMARY for c, _ in sent), "the answer must not leak to the 4M group"


def test_other_peoples_messages_in_a_group_are_ignored(multi, monkeypatch):
    mod, _ = multi
    monkeypatch.setattr(mod, "react", lambda *a, **k: None)
    mod.handle(("m2", DEV, "972500000009@s.whatsapp.net", "2026-11-07 22:11:00+02:00", "הוראה מאדם אחר", False, ""))
    with mod.adb() as db:
        assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == 0


def test_slash_commands_act_on_their_own_conversation(multi):
    mod, _ = multi
    assert "Opus" in mod.owner_command("/מודל opus", DEV)
    assert mod.get_override(DEV) == "opus" and mod.get_override(mod.PRIMARY) is None
    mod.kv_set(mod.K("session_runs", DEV), "5")
    assert "5" in mod.owner_command("/סשן", DEV) and "0" in mod.owner_command("/סשן", mod.PRIMARY)


def test_group_prompt_carries_instructions(multi, tmp_path, monkeypatch):
    mod, _ = multi
    monkeypatch.setattr(mod, "HOME", str(tmp_path))
    os.makedirs(tmp_path / "groups")
    (tmp_path / "groups" / "dev.md").write_text("הוראות פיתוח מיוחדות", encoding="utf-8")
    raw = json.load(open(mod.GROUPS_FILE, encoding="utf-8"))
    raw[DEV]["instructions"] = "groups/dev.md"
    json.dump(raw, open(mod.GROUPS_FILE, "w", encoding="utf-8"))
    gp = mod.group_prompt(DEV)
    assert "הוראות פיתוח מיוחדות" in gp and "handoff-dev.md" in gp
    assert mod.group_prompt(mod.PRIMARY) is None
    (tmp_path / "groups" / "main.md").write_text("- לענות קצר", encoding="utf-8")
    assert "לענות קצר" in mod.group_prompt(mod.PRIMARY), "the control group has its own rules file too"


def test_rule_command(multi, tmp_path, monkeypatch):
    mod, _ = multi
    monkeypatch.setattr(mod, "HOME", str(tmp_path))
    assert "עוד אין כללי בסיס" in mod.owner_command("/כלל בסיס", mod.PRIMARY)
    os.makedirs(tmp_path / "groups")
    (tmp_path / "groups" / "main.md").write_text("- כלל א", encoding="utf-8")
    assert "כלל א" in mod.owner_command("/כלל בסיס", mod.PRIMARY)
    assert mod.owner_command("/כלל בסיס לענות תמיד בקצרה", mod.PRIMARY) is None, "with text it goes to Claude for a check"
    assert mod.rules_rel(DEV).endswith(".md") and mod.rules_rel(mod.PRIMARY) == "groups/main.md"


# ---------- review fixes 08.11: interrupted commands, dev-work on raw text, sticky model, total cap, text approval ----------

def test_interrupted_command_is_not_rerun(multi, monkeypatch):
    mod, _ = multi
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "wait_bridge", lambda *a, **k: True)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('r1', 'עבודה ארוכה', '2026-11-08 07:00', ?, 'wa', 0, 'running')", (DEV,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('p1', 'עוד לא התחיל', '2026-11-08 07:01', ?, 'wa', 0, 'pending')", (DEV,))
    rows = mod.mark_interrupted()
    assert [r[1] for r in rows] == ["r1"]
    mod.notify_interrupted(rows)
    with mod.adb() as db:
        st = dict(db.execute("SELECT msg_id, status FROM commands").fetchall())
    assert st == {"r1": "interrupted", "p1": "pending"}, "running → interrupted (not re-run); pending still answered"
    assert any(c == DEV and "נקטעה" in t for c, t in sent)
    row = mod.claim_next(DEV)
    assert row[1] == "p1"
    with mod.adb() as db:
        assert db.execute("SELECT status, attempts FROM commands WHERE msg_id = 'p1'").fetchone() == ("running", 1)
    assert mod.claim_next(DEV) is None


def test_dev_work_uses_the_owners_own_words(agent):
    mod, _ = agent
    wrapped = mod.T["prompt"].format(owner="יקיר", when="x", text="מה עם הנעליים של דני", where='מהמחשב (לוח הבקרה, "4M")', dest="לחלון")
    assert mod.is_dev_work(wrapped), "the wrapper itself mentions the dashboard"
    attached = "תסדר את הטופס\n" + mod.T["attached"].format(path="C:/Users/me/whatsapp-mcp/whatsapp-bridge/store/x.jpg")
    assert not mod.is_dev_work(mod.raw_text(attached)), "an attachment path is not dev work"
    assert mod.is_dev_work(mod.raw_text("תתקן את הגשר"))


def test_model_is_sticky_within_a_session(multi):
    mod, _ = multi
    mod.CFG.update({"deep_model": "opus", "light_model": "sonnet", "claude_model": "sonnet", "fresh_sessions": False})
    chat = mod.PRIMARY
    # brand-new session: deep words may start it on the deep model
    assert mod.pick_model_info("תכנן לי את השבוע", chat) == ("opus", "auto")
    # running session on sonnet: deep words keep sonnet (cache) and suggest !!
    mod.kv_set(mod.K("session_id", chat), "s1")
    mod.kv_set(mod.K("session_started", chat), mod.dt.datetime.now().isoformat())
    mod.kv_set(mod.K("session_model", chat), "sonnet")
    assert mod.pick_model_info("תכנן לי את השבוע", chat) == ("sonnet", "sticky_deep")
    assert mod.pick_model_info("!! תכנן לי את השבוע", chat) == ("opus", "bang"), "explicit !! still works"
    mod.set_override("haiku", None, chat)
    assert mod.pick_model_info("תכנן", chat) == ("haiku", "forced")
    mod.set_override("auto", None, chat)
    # a session that started on opus keeps opus (no switch mid-session)
    mod.kv_set(mod.K("session_model", chat), "opus")
    assert mod.pick_model_info("מה המצב", chat) == ("opus", "auto")
    # light → strong is the only automatic switch in a running session
    mod.CFG["light_model"] = "haiku"
    mod.kv_set(mod.K("session_model", chat), "haiku")
    assert mod.pick_model_info("תסכם את הדוח", chat) == ("sonnet", "auto")
    mod.kv_set(mod.K("session_model", chat), "sonnet")
    assert mod.pick_model_info("שלום", chat) == ("sonnet", "auto"), "no step down mid-session"
    # yesterday's session rotates: free choice again
    mod.kv_set(mod.K("session_started", chat), "2020-01-01T00:00:00")
    assert mod.pick_model_info("תכנן", chat) == ("opus", "auto")


def test_total_daily_cap(multi):
    mod, _ = multi
    mod.CFG.update({"daily_budget_usd": 5})
    mod.CFG.pop("total_daily_budget_usd", None)
    assert mod.total_budget() == 10.0
    now = mod.dt.datetime.now().isoformat()
    with mod.adb() as db:
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 4.0, ?)", (now, mod.PRIMARY))
        db.execute("INSERT INTO claude_runs (at, cost, chat) VALUES (?, 6.5, ?)", (now, DEV))  # DEV cap is 7
    assert mod.over_budget(DEV) == ("total", 10.5, 10.0), "under its own cap, but the total is used up"
    assert mod.over_budget(DEV, "! בכל זאת") is None, "! lifts the cap for one message"
    mod.CFG["total_daily_budget_usd"] = 50
    assert mod.over_budget(DEV) is None and mod.over_budget(mod.PRIMARY) is None
    mod.CFG["daily_budget_usd"] = 3
    assert mod.over_budget(mod.PRIMARY) == ("chat", 4.0, 3.0)


def test_load_groups_keeps_last_good_copy(multi):
    mod, _ = multi
    assert DEV in mod.load_groups()
    open(mod.GROUPS_FILE, "w", encoding="utf-8").write('{"broken": ')  # caught mid-write
    assert DEV in mod.load_groups(), "a half-written groups.json must not drop the connected groups"
    os.remove(mod.GROUPS_FILE)
    assert DEV not in mod.load_groups(), "a deleted file really means no extra groups"


def test_stop_request_targets_its_conversation(agent):
    mod, _ = agent
    assert mod.stop_applies("1203639999@g.us", "1203639999@g.us", {})
    assert not mod.stop_applies("1203639999@g.us", mod.PRIMARY, {})
    assert mod.stop_applies("", mod.PRIMARY, {"a": 1, "b": 2}), "legacy empty request stops the primary run"
    assert mod.stop_applies("", "1203639999@g.us", {"1203639999@g.us": 1}), "or the only run"
    assert not mod.stop_applies("", "1203639999@g.us", {"x": 1, "1203639999@g.us": 1})


def test_conversations_run_in_parallel(multi, monkeypatch):
    import threading
    mod, _ = multi
    release, done_dev = threading.Event(), threading.Event()

    def fake(prompt, model=None, session=True, timeout=900, on_stage=None, chat=None, **k):
        if chat == mod.PRIMARY:
            release.wait(10)  # a long run in the 4M group…
        else:
            done_dev.set()   # …must not block the dev group
        return "ok"
    monkeypatch.setattr(mod, "send", lambda *a, **k: True)
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: {})
    monkeypatch.setattr(mod, "claude", fake)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('x1', 'ארוך', '2026-11-08 08:00', ?, 'pc', 0)", (mod.PRIMARY,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('x2', 'קצר', '2026-11-08 08:00', ?, 'pc', 0)", (DEV,))
    threading.Thread(target=mod.command_worker, daemon=True).start()
    assert done_dev.wait(8), "the dev group was blocked by the 4M group's run"
    release.set()
    assert _wait_done(mod, 2)


# ---------- 12:49 approvals: limit, sign-in, reconnect, schedule, morning summary, voice ----------

def test_usage_limit_parsing(agent):
    mod, _ = agent
    now = mod.dt.datetime(2026, 11, 8, 10, 17)
    r = mod.parse_limit("You've hit your 5-hour limit · resets 11:30am (Asia/Jerusalem)", now)
    assert r == mod.dt.datetime(2026, 11, 8, 11, 30)
    assert mod.parse_limit("You've hit your limit · resets 3pm", now) == mod.dt.datetime(2026, 11, 8, 15, 0)
    assert mod.parse_limit("You've hit your weekly limit · resets 9am", now) == mod.dt.datetime(2026, 11, 9, 9, 0), "a past hour = tomorrow"
    assert mod.parse_limit("Usage limit reached", now) == now + mod.dt.timedelta(minutes=60), "no time given → +60 min"
    assert mod.parse_limit("הנה סיכום של הדוח", now) is None and mod.parse_limit("", now) is None


def test_usage_limit_parks_and_requeues_commands(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    until = mod.dt.datetime.now() + mod.dt.timedelta(minutes=30)
    mod.kv_set("limit_until", until.isoformat(timespec="seconds"))
    for i in (1, 2):
        mod.enqueue_command(f"L{i}", "מה המצב", "2026-11-08 10:20")
    called = []
    monkeypatch.setattr(mod, "claude", lambda *a, **k: called.append(1) or "x")
    for _ in (1, 2):
        mod.run_command(mod.claim_next(mod.PRIMARY))
    with mod.adb() as db:
        st = [r[0] for r in db.execute("SELECT status FROM commands ORDER BY id")]
    assert st == ["waiting_limit", "waiting_limit"] and not called, "no Claude run while the limit is on"
    assert sum("מכסת השימוש" in t for t in sent) == 1, "one notice per window, not per command"
    assert mod.release_limit_waiters() == 0, "still waiting"
    mod.kv_set("limit_until", (mod.dt.datetime.now() - mod.dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert mod.release_limit_waiters() == 2
    resumed = [t for t in sent if "▶️" in t]
    assert len(resumed) == 1 and "2 הודעות" in resumed[0] and "10:20" in resumed[0], "one notice listing what arrived"
    with mod.adb() as db:
        assert [r[0] for r in db.execute("SELECT status FROM commands ORDER BY id")] == ["pending", "pending"]


def test_limit_hit_during_a_run_parks_the_command(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    until = mod.dt.datetime.now() + mod.dt.timedelta(minutes=40)

    def hit(*a, **k):
        raise mod.LimitHit(until)
    monkeypatch.setattr(mod, "_run_claude", hit)
    mod.enqueue_command("L9", "תסכם לי את היום", "2026-11-08 10:20")
    mod.run_command(mod.claim_next(mod.PRIMARY))
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands").fetchone()[0] == "waiting_limit"
    assert any(until.strftime("%H:%M") in t for t in sent)


def test_background_runs_skip_quietly_during_the_limit(agent, monkeypatch):
    mod, _ = agent
    import pytest as _pt
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(minutes=5)).isoformat(timespec="seconds"))

    def boom(*a, **k):
        raise AssertionError("must not run")
    monkeypatch.setattr(mod, "_run_claude", boom)
    assert mod.claude("x", session=False) is None
    with _pt.raises(mod.LimitHit):
        mod.claude("x", on_stage=lambda *a: None)


def test_signin_expiry_parsing_and_warning(agent, monkeypatch):
    mod, _ = agent
    err = "Warning: this sign-in ends between 2026-11-11 and 2026-11-14 (in 3 days at the earliest). Renewing it does not move those dates."
    assert mod.parse_signin(err) == ("2026-11-11", "2026-11-14")
    assert mod.parse_signin("nothing here") is None
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    soon = (mod.dt.date.today() + mod.dt.timedelta(days=2)).isoformat()
    mod.kv_set("signin_ends", f"{soon}|{soon}")
    mod.signin_check()
    assert sent and "claude auth login" in sent[-1]
    sent.clear()
    far = (mod.dt.date.today() + mod.dt.timedelta(days=20)).isoformat()
    mod.kv_set("signin_ends", f"{far}|{far}")
    mod.signin_check()
    assert not sent


def test_reconnect_finds_missed_owner_messages(multi, monkeypatch):
    mod, db = multi
    db.execute("CREATE TABLE IF NOT EXISTS conn_log (at TIMESTAMP, event TEXT)")
    now = mod.dt.datetime.now().replace(microsecond=0)
    start = now - mod.dt.timedelta(minutes=20)
    db.execute("INSERT INTO conn_log VALUES (?, 'disconnected')", ((start - mod.dt.timedelta(seconds=40)).strftime("%Y-%m-%d %H:%M:%S"),))
    db.execute("INSERT INTO conn_log VALUES (?, 'logged_out')", ((start - mod.dt.timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S"),))

    def ts(m):
        return (now - mod.dt.timedelta(minutes=m)).astimezone().isoformat(sep=" ", timespec="seconds")
    rows = [("O1", DEV, "me", "מה המצב עם הגשר", ts(15), 1, ""),       # owner, during the outage, never handled -> missed
            ("O2", mod.PRIMARY, "me", "🤖 _תשובה_", ts(10), 1, ""),     # the bot's own reply -> ignored
            ("O3", DEV, "x", "הודעה של מישהו אחר", ts(12), 0, ""),      # not the owner -> ignored
            ("O4", DEV, "me", "לפני הניתוק", ts(60), 1, ""),             # before the outage -> ignored
            ("O5", mod.PRIMARY, "me", "כבר טופלה", ts(5), 1, "")]       # already handled -> ignored
    db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, '')", rows)
    db.commit()
    with mod.adb() as a:
        a.execute("INSERT INTO handled VALUES ('O5', ?, 'x')", (mod.PRIMARY,))
    s, e = mod.outage_window(start, now)
    assert s < start, "the bridge's own 'disconnected' time is used when it is earlier"
    assert [r[0] for r in mod.missed_owner_messages(s, e)] == ["O1"]
    assert "QR" in mod.outage_reason(s, e)
    handled, sent = [], []
    monkeypatch.setattr(mod, "handle", lambda r: handled.append(r[0]))
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    mod.report_reconnect(start, now, False, settle=0)
    assert handled == ["O1"]
    assert sent and sent[-1][0] == mod.PRIMARY and "נמצאו 1" in sent[-1][1] and "20" in sent[-1][1]
    # a short blip with nothing missed stays quiet
    sent.clear()
    mod.report_reconnect(now - mod.dt.timedelta(seconds=50), now, False, settle=0)
    assert not sent


def test_schedule_time_parsing(agent):
    mod, _ = agent
    now = mod.dt.datetime(2026, 11, 8, 13, 0)
    d = mod.dt.datetime
    assert mod.parse_send_time("08:00", now) == d(2026, 11, 9, 8, 0), "a passed hour today = tomorrow"
    assert mod.parse_send_time("ב-17:30", now) == d(2026, 11, 8, 17, 30)
    assert mod.parse_send_time("מחר 08:00", now) == d(2026, 11, 9, 8, 0)
    assert mod.parse_send_time("שלח ב 8", now) == d(2026, 11, 9, 8, 0)
    assert mod.parse_send_time("מחר ב-9", now) == d(2026, 11, 9, 9, 0)
    assert mod.parse_send_time("15:00.", now) == d(2026, 11, 8, 15, 0)
    for no in ("תשנה את השעה ל-08:00 בטיוטה", "25:00", "שלום", "", "8 קסדות"):
        assert mod.parse_send_time(no, now) is None, no


def test_reply_to_card_schedules_and_sends(agent, monkeypatch):
    mod, db = agent
    db.execute("ALTER TABLE messages ADD COLUMN quoted_id TEXT")
    db.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, recipient TEXT, status TEXT, card_id TEXT)")
    db.execute("INSERT INTO drafts VALUES (7, '972500000009@s.whatsapp.net', 'pending', 'CARD7')")
    db.execute("INSERT INTO messages VALUES ('R1', ?, 'me', '20:00', '2026-11-08 13:00:00+02:00', 1, '', '', 'CARD7')", (mod.PRIMARY,))
    db.commit()
    sent, decided = [], []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, **k: decided.append((path, body)) or {"success": True})
    assert mod.schedule_from_reply(mod.PRIMARY, "R1", "20:00")
    assert "טיוטה #7" in sent[-1]
    with mod.adb() as a:
        a.execute("UPDATE scheduled_drafts SET send_at = '2000-01-01T00:00:00'")
    mod.scheduled_check()
    assert decided == [("/draft_decide", {"id": 7, "approve": True})]
    mod.scheduled_check()
    assert len(decided) == 1, "a schedule fires once"
    # cancel on a card whose schedule already fired: nothing to cancel -> ordinary message
    assert not mod.schedule_from_reply(mod.PRIMARY, "R1", "בטל")
    # not a reply to a card -> an ordinary message
    db.execute("INSERT INTO messages VALUES ('R2', ?, 'me', '20:00', '2026-11-08 13:01:00+02:00', 1, '', '', NULL)", (mod.PRIMARY,))
    db.commit()
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    assert not mod.schedule_from_reply(mod.PRIMARY, "R2", "20:00")


def test_schedule_skips_a_draft_that_is_no_longer_pending(agent, monkeypatch):
    mod, db = agent
    db.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, recipient TEXT, status TEXT, card_id TEXT)")
    db.execute("INSERT INTO drafts VALUES (8, 'x', 'sent', 'CARD8')")
    db.commit()
    sent, decided = [], []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, **k: decided.append(body) or {"success": True})
    with mod.adb() as a:
        a.execute("INSERT INTO scheduled_drafts (draft_id, send_at, chat) VALUES (8, '2000-01-01T00:00:00', ?)", (mod.PRIMARY,))
    mod.scheduled_check()
    assert not decided and "כבר לא ממתינה" in sent[-1]


def test_morning_summary_extract(multi):
    mod, db = multi
    db.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    db.execute("INSERT INTO chats VALUES ('972500000002@s.whatsapp.net', 'דנה', NULL)")
    db.execute("INSERT INTO chats VALUES ('972500000003@s.whatsapp.net', 'רון', NULL)")
    now = mod.dt.datetime.now().astimezone().replace(microsecond=0)

    def ts(m):
        return (now - mod.dt.timedelta(minutes=m)).isoformat(sep=" ")
    db.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, '')", [
        ("a", "972500000002@s.whatsapp.net", "972500000002", "אתה מגיע מחר?", ts(50), 0, ""),   # Dana waits for an answer
        ("b", "972500000003@s.whatsapp.net", "972500000003", "תודה", ts(40), 0, ""),
        ("c", "972500000003@s.whatsapp.net", "me", "בשמחה", ts(30), 1, ""),                      # Ron was answered
        ("d", mod.PRIMARY, "me", "הודעה בקבוצת הבקרה", ts(20), 1, ""),                            # control group -> excluded
        ("e", "120363777@g.us", "972500000004", "", ts(10), 0, "sticker"),                         # sticker-only chat -> skipped
    ])
    db.commit()
    lines, waiting = mod.summary_extract(now - mod.dt.timedelta(hours=2), now)
    text = "\n".join(lines)
    assert "## דנה" in text and "## רון" in text and "קבוצת הבקרה" not in text and "120363777" not in text
    assert [w[0] for w in waiting] == ["דנה"]


def test_tts_text_cleaning(agent):
    mod, _ = agent
    t = mod.tts_clean("*סיכום:* _הכול_ תקין ✅🙂\nראה https://example.com/x\n`code`")
    assert "*" not in t and "_" not in t and "✅" not in t and "http" not in t and "code" not in t
    assert "סיכום" in t and "קישור" in t
    c = mod.tts_clean("משפט ארוך שחוזר על עצמו. " * 100)
    assert len(c) < 900 and c.endswith("ההמשך בהודעה הכתובה.")


def test_auto_update_is_scheduled_and_reports(agent, monkeypatch, tmp_path):
    mod, _ = agent
    calls = []
    monkeypatch.setattr(mod, "reply", lambda t, *a, **k: calls.append(t))
    monkeypatch.setattr(mod, "wait_bridge", lambda *a, **k: True)
    monkeypatch.setattr(mod, "HERE", str(tmp_path))  # never touch the real update.log
    mod.report_auto_update()
    assert calls == [], "nothing pending -> silent"
    mod.kv_set("auto_update_pending", '{"from": "1.0.8", "to": "1.0.9"}')
    (tmp_path / "update.log").write_text("[x] \u05de\u05e2\u05d3\u05db\u05df 1.0.8 v1.0.9\n[x] ok 1.0.9\n", encoding="utf-8")
    mod.report_auto_update()
    assert calls and "עודכן אוטומטית" in calls[-1]
    assert mod.kv_get("auto_update_pending") == ""
    mod.kv_set("auto_update_pending", '{"from": "1.0.8", "to": "1.0.9"}')
    (tmp_path / "update.log").write_text("[x] \u05de\u05e2\u05d3\u05db\u05df 1.0.8 v1.0.9\n[x] \u21a9 back 1.0.8\n", encoding="utf-8")
    mod.report_auto_update()
    assert "נכשל" in calls[-1]


def test_tunnel_command_both_ways(agent):
    mod, _ = agent
    cmd = mod.tunnel_cmd({"name": "נייח", "url": "http://127.0.0.1:8781", "tunnel": "ybhw"}, ssh="ssh")
    assert cmd[0] == "ssh" and cmd[-1] == "ybhw" and "-N" in cmd and "BatchMode=yes" in cmd
    assert cmd[cmd.index("-L") + 1] == "8781:127.0.0.1:8770" and cmd[cmd.index("-R") + 1] == "8781:127.0.0.1:8770"
    assert mod.peer_port({"url": "http://127.0.0.1:9001"}) == 9001 and mod.peer_port({}) == 8781


def test_paused_conversation_keeps_commands_queued(agent, monkeypatch):
    mod, _ = agent
    notes = []
    monkeypatch.setattr(mod, "reply", lambda t, *a, **k: notes.append((mod._ctx.chat, t)))
    started = []
    monkeypatch.setattr(mod, "conversation_worker", lambda chat: None)  # a leftover dispatcher thread from another test must not count
    mod.enqueue_command("m1", "hello", "2026-11-08 10:00", chat=mod.PRIMARY)
    mod.kv_set("listen_paused", "2026-11-08T10:00:00")
    assert mod.listening_paused(mod.PRIMARY)
    # one dispatcher round, by hand (the loop body)
    with mod.adb() as db:
        chats = [r[0] for r in db.execute("SELECT DISTINCT coalesce(chat_jid, ?) FROM commands WHERE status = 'pending'", (mod.PRIMARY,))]
    for chat in chats:
        p = mod.listening_paused(chat)
        if p:
            mod.pause_notice(chat, p)
            continue
        started.append(chat)
    mod.pause_notice(mod.PRIMARY, "2026-11-08T10:00:00")  # second time: no second note
    assert started == [] and len(notes) == 1 and "מושהית" in notes[0][1]
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands").fetchall() == [("pending",)], "nothing is lost while paused"
    mod.kv_set("listen_paused", "")
    assert not mod.listening_paused(mod.PRIMARY)



def test_peer_watch_alerts_once_and_reports_recovery(agent):
    mod, _ = agent
    cfg = {"peer_offline_minutes": 30, "peer_alert_minutes": 10}
    t0 = mod.dt.datetime(2026, 11, 8, 20, 0)
    m = lambda n: t0 + mod.dt.timedelta(minutes=n)
    st, out = mod.peer_watch_step({}, None, t0, cfg, "נייח")
    assert out == [] and st["off_since"]
    st, out = mod.peer_watch_step(st, None, m(20), cfg, "נייח")
    assert out == [], "not before peer_offline_minutes"
    st, out = mod.peer_watch_step(st, None, m(31), cfg, "נייח")
    assert len(out) == 1 and "לא זמין" in out[0] and "שינה" in out[0]
    st, out = mod.peer_watch_step(st, None, m(40), cfg, "נייח")
    assert out == [], "one alert per incident"
    ok = {"agent_alive": True, "bridge": {"connected": True}}
    st, out = mod.peer_watch_step(st, ok, m(45), cfg, "נייח")
    assert len(out) == 1 and "חזר" in out[0] and "45" in out[0]
    down = {"agent_alive": True, "bridge": {"connected": False}}
    st, out = mod.peer_watch_step(st, down, m(50), cfg, "נייח")
    st, out = mod.peer_watch_step(st, down, m(61), cfg, "נייח")
    assert len(out) == 1 and "הגשר" in out[0]
    st, out = mod.peer_watch_step(st, ok, m(70), cfg, "נייח")
    assert len(out) == 1 and "חזר להתחבר" in out[0]
    st, out = mod.peer_watch_step({}, down, m(0), cfg, "נייח")
    st, out = mod.peer_watch_step(st, ok, m(5), cfg, "נייח")
    assert out == [], "a short blip that never alerted stays quiet"


def test_peer_backup_plan_newest_first_within_cap(agent):
    mod, _ = agent
    local = [("a/old.jpg", 100, 1), ("a/new.jpg", 300, 9), ("b/mid.ogg", 200, 5), ("b/have.ogg", 50, 7)]
    plan, left = mod.peer_backup_plan(local, {"b\\have.ogg"}, 520)
    assert [r for r, _, _ in plan] == ["a/new.jpg", "b/mid.ogg"] and left == 1
    plan, left = mod.peer_backup_plan(local, set(), 10_000)
    assert len(plan) == 4 and left == 0
    assert mod.peer_alias({"tunnel": "ybhw"}) == "ybhw" and mod.peer_alias({"ssh": "4-MINE", "tunnel": "x"}) == "4-MINE"


def test_transcribe_gap_finds_untranscribed_voice_notes(agent):
    mod, db = agent
    prim = mod.PRIMARY
    db.execute("INSERT INTO messages VALUES ('v1', ?, 'me', '', '2026-11-08 10:00:00+02:00', 1, 'audio', 'a.ogg')", (prim,))
    db.execute("INSERT INTO messages VALUES ('v2', ?, 'me', '', '2026-11-08 11:00:00+02:00', 1, 'audio', 'b.ogg')", (prim,))
    db.execute("INSERT INTO messages VALUES ('v0', ?, 'me', '', '2026-11-01 11:00:00+02:00', 1, 'audio', 'c.ogg')", (prim,))
    db.execute("INSERT INTO messages VALUES ('o1', '972500000001@s.whatsapp.net', 'x', '', '2026-11-08 12:00:00+02:00', 0, 'audio', 'd.ogg')")
    db.commit()
    with mod.adb() as a:
        a.execute("INSERT INTO transcripts VALUES ('v2', ?, 'me', '2026-11-08 11:00', 'שלום', 'm')", (prim,))
    mod.kv_set("transcribe_off_since", "2026-11-08T09:00:00")
    mod.CFG["transcribe_all_chats"] = False
    since, todo = mod.transcribe_gap()
    assert since.startswith("2026-11-08T09") and [r[0] for r in todo] == ["v1"], "only the owner's control-group notes, untranscribed, in the gap"
    mod.CFG["transcribe_all_chats"] = True
    since, todo = mod.transcribe_gap()
    assert [r[0] for r in todo] == ["v1", "o1"], "oldest first, every chat"
    mod.kv_set("transcribe_backfill_request", "x")
    mod.transcribe_backfill_check()
    assert mod.kv_get("transcribe_backfill_request") == "" and mod.slow.qsize() >= 1


def test_status_hourglass_flips(agent):
    mod, _ = agent
    st = mod.StatusMsg("sonnet", wa=False)
    a = st._text()
    st.tick += 1
    b = st._text()
    st.finish()
    assert "\u23f3" in a and "\u231b" in b, "the hourglass alternates on every edit"


def test_local_health_alerts_once_after_delay_and_resets(agent):
    mod, _ = agent
    t0 = mod.dt.datetime(2026, 11, 8, 20, 0)
    m = lambda n: t0 + mod.dt.timedelta(minutes=n)
    bad = {"connected": False, "health": {"problem": "outdated", "detail": "405"}}
    st, msg = mod.local_health_step({}, bad, t0, {"health_alert_minutes": 5})
    assert msg is None and st["problem"] == "outdated"
    st, msg = mod.local_health_step(st, bad, m(6), {"health_alert_minutes": 5})
    assert msg and "עדכון" in msg and "405" in msg
    st, msg = mod.local_health_step(st, bad, m(20), {})
    assert msg is None, "one alert per incident"
    st, msg = mod.local_health_step(st, {"connected": True, "health": {}}, m(21), {})
    assert st == {} and msg is None
    st, msg = mod.local_health_step({}, {"connected": False}, t0, {})
    assert st["problem"] == "down"


def test_peer_alert_names_the_bridge_problem(agent):
    mod, _ = agent
    t0 = mod.dt.datetime(2026, 11, 8, 20, 0)
    brief = {"agent_alive": True, "bridge": {"connected": True, "health": {"problem": "outdated", "detail": "405"}}}
    st, out = mod.peer_watch_step({}, brief, t0, {"peer_alert_minutes": 10}, "לפטופ")
    st, out = mod.peer_watch_step(st, brief, t0 + mod.dt.timedelta(minutes=11), {"peer_alert_minutes": 10}, "לפטופ")
    assert len(out) == 1 and "סיבה:" in out[0] and "עדכון" in out[0]


def test_stage_icon():
    import importlib, sys
    sys.path.insert(0, HERE)
    mod = importlib.import_module("agent")
    assert mod.stage_icon("⚙️ מריץ פקודה") == "⚙️"
    assert mod.stage_icon("🧠 מתחיל") == "🧠"
    assert mod.stage_icon("מתחיל") == ""


def test_follow_ups_missed_calls_and_unread_sends(agent):
    mod, db = agent
    db.execute("CREATE TABLE calls (call_id TEXT, caller TEXT, event TEXT, reason TEXT, at TIMESTAMP)")
    db.execute("CREATE TABLE receipts (message_id TEXT, chat_jid TEXT, reader TEXT, type TEXT, at TIMESTAMP)")
    db.execute("CREATE TABLE send_log (id INTEGER PRIMARY KEY, recipient TEXT, message_id TEXT, ok BOOLEAN, detail TEXT, timestamp TIMESTAMP)")
    db.executemany("INSERT INTO calls VALUES (?, ?, ?, '', ?)", [
        ("C1", "972500000001", "offer", "2026-11-08 09:00:00"),
        ("C2", "972500000002", "offer", "2026-11-08 09:10:00"), ("C2", "972500000002", "accept", "2026-11-08 09:10:05")])
    db.execute("INSERT INTO receipts VALUES ('R0', 'x', 'x', 'read', '2026-11-08 07:00:00')")
    db.executemany("INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES (?, ?, 1, '', ?)", [
        ("972500000003@s.whatsapp.net", "S1", "2026-11-08 08:00:00+02:00"),
        ("972500000004@s.whatsapp.net", "S2", "2026-11-08 08:00:00+02:00"),
        ("972500000005@s.whatsapp.net", "S0", "2026-11-08 06:00:00+02:00")])  # before receipts were tracked
    db.execute("INSERT INTO receipts VALUES ('S2', 'x', 'x', 'read', '2026-11-08 08:30:00')")
    db.commit()
    out = mod.follow_ups(mod.dt.datetime(2026, 11, 8, 0, 0), mod.dt.datetime(2026, 11, 8, 12, 0))
    assert any("שיחה שלא נענתה" in x and "972500000001" in x for x in out)
    assert not any("972500000002" in x for x in out), "an answered call is not missed"
    assert any("972500000003" in x and "לא קרא" in x for x in out)
    assert not any("972500000004" in x or "972500000005" in x for x in out)

def test_stage_icons_for_commands_and_git(agent):
    mod, _ = agent
    ev = lambda c: {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": c}}]}}
    assert mod._stage_of(ev("dir")).startswith("\u2b1b")
    assert "GitHub" in mod._stage_of(ev("git push origin enhanced:main"))  # network = GitHub
    assert mod._stage_of(ev("git push hub HEAD:main")).startswith("\U0001f501")  # the other computer, not GitHub
    assert "\u05de\u05e7\u05d5\u05de\u05d9" in mod._stage_of(ev("git commit -m x"))  # local
    assert mod._stage_of(ev("git -C C:/x pull --ff-only")).startswith("\U0001f501")
    assert "\U0001f512" in mod.T["budget_stop"] and "!!!" in mod.T["budget_stop"]


def test_day_sync_plan():
    import importlib, sys
    sys.path.insert(0, HERE)
    mod = importlib.import_module("agent")
    assert mod.day_sync_plan(["whatsapp-bridge/health.go"]) == (True, False, True)
    assert mod.day_sync_plan(["agent/agent.py", "README.md"]) == (False, True, False)
    assert mod.day_sync_plan(["README.md"]) == (False, False, False)


def test_day_sync_does_not_save_when_tests_fail(agent, monkeypatch):
    mod, _ = agent
    calls, said = [], []
    def fake_git(*args, timeout=120):
        calls.append(args)
        if args[0] == "remote":
            return 0, "hub\norigin"
        if args[0] == "status":
            return 0, " M agent/agent.py"
        if args[0] == "rev-parse":
            return 0, "abc"
        return 0, ""
    monkeypatch.setattr(mod, "_git", fake_git)
    monkeypatch.setattr(mod, "_day_sync_tests", lambda go, py: "1 failed")
    monkeypatch.setattr(mod, "reply", lambda text, **k: said.append(text))
    mod.day_sync()
    assert not any(a[0] in ("add", "commit") or (len(a) > 4 and a[4] == "commit") for a in calls)
    assert said and "לא נשמרו" in said[0] and "1 failed" in said[0]
    assert ("push", "hub", "HEAD:main") in calls, "what is already committed is still pushed"


def _fake_git_merge(calls, merge_rc=0, conflicted="", leftover=""):
    def fake_git(*args, timeout=120):
        calls.append(args)
        a = [x for x in args if x != "-c" and "=" not in x]
        if a[:2] == ["merge", "--ff-only"]:
            return 1, "fatal: Not possible to fast-forward"
        if a[:1] == ["merge"] and "hub/main" in a:
            return merge_rc, "CONFLICT" if merge_rc else "Merge made"
        if a[:1] == ["diff"] and "--diff-filter=U" in a:
            return 0, conflicted
        if a[:1] == ["grep"]:
            return (0 if leftover else 1), leftover
        if a[:1] == ["diff"]:
            return 0, "agent/agent.py"
        if a[:1] == ["rev-parse"]:
            return 0, "abc"
        return 0, ""
    return fake_git


def test_hub_sync_merges_both_sides_and_runs_tests(agent, monkeypatch):
    mod, _ = agent
    calls, resolved = [], []
    monkeypatch.setattr(mod, "_git", _fake_git_merge(calls, merge_rc=1, conflicted="agent/agent.py"))
    monkeypatch.setattr(mod, "_resolve_conflicts", lambda files: resolved.append(files) or "")
    monkeypatch.setattr(mod, "_day_sync_tests", lambda go, py: "")
    ok, note = mod._hub_sync()
    assert ok and "🔀" in note and resolved == [["agent/agent.py"]]
    assert any("commit" in c for c in calls), "the combined result is committed"
    assert not any(c[:1] == ("reset",) for c in calls)


def test_hub_sync_undoes_merge_when_tests_fail(agent, monkeypatch):
    mod, _ = agent
    calls = []
    monkeypatch.setattr(mod, "_git", _fake_git_merge(calls))
    monkeypatch.setattr(mod, "_day_sync_tests", lambda go, py: "2 failed")
    ok, note = mod._hub_sync()
    assert not ok and "2 failed" in note
    assert ("reset", "--keep", "abc") in calls, "undo keeps our own commits (no --hard)"


def test_hub_sync_aborts_when_markers_remain(agent, monkeypatch):
    mod, _ = agent
    calls = []
    monkeypatch.setattr(mod, "_git", _fake_git_merge(calls, merge_rc=1, conflicted="a.py", leftover="a.py"))
    monkeypatch.setattr(mod, "_resolve_conflicts", lambda files: "")
    ok, note = mod._hub_sync()
    assert not ok and ("merge", "--abort") in calls and not any("commit" in c for c in calls)


def test_peers_included(agent, monkeypatch):
    mod, _ = agent
    mod.CFG["peers"] = [{"name": "laptop", "url": "http://x"}]
    monkeypatch.setattr(mod, "_git", lambda *a, timeout=120: (0, ""))
    monkeypatch.setattr(mod, "peer_fetch", lambda p, path="/brief", timeout=5: None)
    assert "laptop" in mod._peers_included()
    monkeypatch.setattr(mod, "peer_fetch", lambda p, path="/brief", timeout=5: {"git": {"head": "abc", "dirty": 2}})
    assert "2" in mod._peers_included()
    monkeypatch.setattr(mod, "peer_fetch", lambda p, path="/brief", timeout=5: {"git": {"head": "abc", "dirty": 0}})
    assert mod._peers_included() == ""
    monkeypatch.setattr(mod, "_git", lambda *a, timeout=120: (1, ""))  # its HEAD is not inside ours
    assert "abc" in mod._peers_included()


def test_day_sync_skips_without_hub(agent, monkeypatch):
    mod, _ = agent
    said = []
    monkeypatch.setattr(mod, "_git", lambda *a, timeout=120: (0, "origin"))
    monkeypatch.setattr(mod, "reply", lambda text, **k: said.append(text))
    mod.day_sync()
    assert said == []


def test_group_name_follows_model(agent, monkeypatch):
    mod, _ = agent
    calls = []
    def fake_bridge(path, body=None, timeout=180, tok=None):
        calls.append((path, body))
        if path == "/group_info":
            return {"success": True, "name": "\u05d2\u05e9\u05e8 \u05e7\u05dc\u05d0\u05d5\u05d3 \u05d5\u05d5\u05e6\u05d0\u05e4"}
        return {"success": True}
    monkeypatch.setattr(mod, "bridge", fake_bridge)
    mod.CFG["group_name_model"] = True
    mod.sync_group_name(mod.PRIMARY, "claude-opus-5-5")
    names = [b["name"] for p, b in calls if p == "/set_group"]
    assert names == ["\u05d2\u05e9\u05e8 \u05d0\u05d5\u05e4\u05d5\u05e1 \u05d5\u05d5\u05e6\u05d0\u05e4"], names  # גשר אופוס ווצאפ
    mod.sync_group_name(mod.PRIMARY, "opus")  # same name: no second rename
    assert len([1 for p, b in calls if p == "/set_group"]) == 1
    mod.kv_set(mod.K("name_set_at", mod.PRIMARY), "0")
    mod.sync_group_name(mod.PRIMARY, "sonnet")
    assert [b["name"] for p, b in calls if p == "/set_group"][-1].split()[1] == "\u05e1\u05d5\u05e0\u05d8"  # סונט


def test_sunglasses_reads_last_answer(agent, monkeypatch, tmp_path):
    mod, _ = agent
    import sqlite3 as _sq
    db = _sq.connect(mod.MSG_DB if hasattr(mod, "MSG_DB") else ":memory:")
    spoken, sent = [], []
    monkeypatch.setattr(mod, "tts_ogg", lambda text, path, voice=None: (spoken.append(text), path)[1])
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, **k: sent.append((chat, media)))
    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, args):
            class R:
                def fetchone(_s): return (mod.CFG["reply_prefix"] + "_hello there_",)
            return R()
    monkeypatch.setattr(mod, "mdb", lambda: FakeDB())
    mod.read_aloud(mod.PRIMARY)
    assert spoken and "hello there" in spoken[0] and sent and sent[0][1]


def test_wheel_command_toggles_read_all(agent):
    mod, _ = agent
    a = mod.owner_command("/\U0001f6de", mod.PRIMARY)
    assert mod.kv_get(mod.K("read_all", mod.PRIMARY)) == "1" and a
    b = mod.owner_command("/\U0001f6de", mod.PRIMARY)
    assert not mod.kv_get(mod.K("read_all", mod.PRIMARY)) and b != a
