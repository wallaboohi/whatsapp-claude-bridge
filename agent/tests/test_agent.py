"""Agent tests that need no WhatsApp account: a temporary store + agent database."""
import importlib
import json
import os
import sqlite3
import sys
import time

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
                        ("RESTART_FILE", "restart.request"), ("STOP_FILE", "stop.request"),
                        ("PROVIDER_FILE", "provider.json")):
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
    mod._migrated.clear()
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
    assert any(r[0] == DEV and r[1] == "tool" and r[2].startswith("🧠 ") and r[2].endswith("התשובה") for r in rows)  # owner 13.11: the answer opens with who answered
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

def test_interrupted_command_is_resumed(multi, monkeypatch):
    """Owner 11.11: a command cut off by a restart / sleep is resumed (Claude first checks what was already done);
    after RESUME_MAX_ATTEMPTS tries it is not run again."""
    mod, _ = multi
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "wait_bridge", lambda *a, **k: True)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status, attempts) VALUES ('r1', 'עבודה ארוכה', '2026-11-08 07:00', ?, 'wa', 0, 'running', 1)", (DEV,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status, attempts) VALUES ('r2', 'קורסת', '2026-11-08 06:00', ?, 'wa', 0, 'running', 3)", (DEV,))
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status) VALUES ('p1', 'עוד לא התחיל', '2026-11-08 07:01', ?, 'wa', 0, 'pending')", (DEV,))
    rows = mod.mark_interrupted()
    assert sorted((r[1], r[5]) for r in rows) == [("r1", True), ("r2", False)]
    mod.notify_interrupted(rows)
    with mod.adb() as db:
        st = dict(db.execute("SELECT msg_id, status FROM commands").fetchall())
    assert st == {"r1": "pending", "r2": "interrupted", "p1": "pending"}
    assert any(c == DEV and "ממשיך" in t for c, t in sent) and any(c == DEV and "נקטעה" in t and "לא מריץ" in t for c, t in sent)
    row = mod.claim_next(DEV)
    assert row[1] == "r1", "the cut command first, then the rest in order"
    prompts = []
    monkeypatch.setattr(mod, "claude", lambda prompt, **k: prompts.append(prompt) or "סיימתי")
    monkeypatch.setattr(mod, "react", lambda *a: None)
    mod.run_command(row)
    assert prompts and "נקטעה באמצע" in prompts[0] and "אל תכפיל" in prompts[0]
    assert not mod.kv_get(f"cut:{row[0]}"), "the resume note is used once"
    row = mod.claim_next(DEV)
    assert row[1] == "p1"
    mod.run_command(row)
    assert "נקטעה באמצע" not in prompts[1]


def test_limit_mid_run_resumes_with_a_check(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    mod.enqueue_command("M1", "בנה דוח", "2026-11-11 01:00")
    soon = mod.dt.datetime.now() + mod.dt.timedelta(minutes=5)

    def hit(*a, **k):
        mod.kv_set("limit_until", soon.isoformat(timespec="seconds"))
        raise mod.LimitHit(soon)
    monkeypatch.setattr(mod, "claude", hit)
    row = mod.claim_next(mod.PRIMARY)
    mod.run_command(row)
    assert mod.kv_get(f"cut:{row[0]}")
    mod.kv_set("limit_until", (mod.dt.datetime.now() - mod.dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert mod.release_limit_waiters() == 1
    prompts = []
    monkeypatch.setattr(mod, "claude", lambda prompt, **k: prompts.append(prompt) or "ok")
    mod.run_command(mod.claim_next(mod.PRIMARY))
    assert "מכסת השימוש נגמרה באמצע" in prompts[0]


def test_scheduled_job_waits_for_the_limit(agent, monkeypatch):
    mod, _ = agent
    sent, ran = [], []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "send_outbox", lambda *a: None)
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(minutes=30)).isoformat(timespec="seconds"))
    mod.run_job({"name": "דוח לילה", "prompt": "x"}, "🌙")
    assert any("מכסת השימוש" in t for t in sent) and not any("נכשלה" in t for t in sent)
    monkeypatch.setattr(mod, "run_deferred", lambda items: ran.extend(items))
    monkeypatch.setattr(mod.threading, "Thread", lambda target, args=(), daemon=None: type("T", (), {"start": lambda self: target(*args)})())
    mod.kv_set("limit_until", (mod.dt.datetime.now() - mod.dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    mod.release_limit_waiters()
    assert ran and ran[0]["job"]["name"] == "דוח לילה"
    assert not mod.kv_get("limit_deferred")


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
    monkeypatch.setattr(mod, "claude_usage", lambda max_age=120: None)  # the bridge words the limit; no real meter call
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
    assert sum("הודעה מהגשר" in t for t in sent) == 2, "owner 13.11: each message is acknowledged once, by the bridge"
    assert mod.release_limit_waiters() == 0, "still waiting"
    mod.kv_set("limit_until", (mod.dt.datetime.now() - mod.dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    assert mod.release_limit_waiters() == 2
    resumed = [t for t in sent if "▶️" in t]
    assert len(resumed) == 1 and "2 הודעות" in resumed[0] and "10:20" in resumed[0], "one notice listing what arrived"
    with mod.adb() as db:
        assert [r[0] for r in db.execute("SELECT status FROM commands ORDER BY id")] == ["pending", "pending"]


def test_stranded_limit_waiters_are_requeued(agent, monkeypatch):
    """Owner 11.11: the limit flag was cleared but the PC slept / the notice hung before the re-queue, so the commands sat
    in waiting_limit forever. Now: any waiting_limit command with no active limit goes back to the queue, notice after."""
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    mod.enqueue_command("S1", "מה המצב", "2026-11-10 22:42")
    with mod.adb() as db:
        db.execute("UPDATE commands SET status = 'waiting_limit'")
    mod.kv_set("limit_until", "")
    assert mod.release_limit_waiters() == 1
    with mod.adb() as db:
        assert [r[0] for r in db.execute("SELECT status FROM commands")] == ["pending"]
    assert any("▶️" in t and "22:42" in t for t in sent)
    assert mod.release_limit_waiters() == 0


def test_requeue_happens_even_if_the_notice_fails(agent, monkeypatch):
    mod, _ = agent
    def boom(*a, **k):
        raise RuntimeError("bridge hung")
    monkeypatch.setattr(mod, "resume_notice", boom)
    mod.enqueue_command("S2", "x", "2026-11-10 23:00")
    with mod.adb() as db:
        db.execute("UPDATE commands SET status = 'waiting_limit'")
    mod.kv_set("limit_until", (mod.dt.datetime.now() - mod.dt.timedelta(minutes=1)).isoformat(timespec="seconds"))
    try:
        mod.release_limit_waiters()
    except RuntimeError:
        pass
    with mod.adb() as db:
        assert [r[0] for r in db.execute("SELECT status FROM commands")] == ["pending"]


def test_limit_hit_during_a_run_parks_the_command(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "claude_usage", lambda max_age=120: None)  # the bridge words the limit; no real meter call
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
    # exact end from `claude auth status` first
    exp = {"v": (mod.dt.datetime.now(mod.dt.timezone.utc) + mod.dt.timedelta(days=2)).isoformat().replace("+00:00", "Z")}
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": mod.json.dumps({"loginExpiresAt": exp["v"]})})())
    mod.signin_check()
    assert sent and "claude auth login" in sent[-1]
    sent.clear()
    exp["v"] = (mod.dt.datetime.now(mod.dt.timezone.utc) + mod.dt.timedelta(days=20)).isoformat().replace("+00:00", "Z")
    mod.signin_check()
    assert not sent
    # fallback: no expiry in the status -> the estimate from the run output
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "{}"})())
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
    assert "הטיוטה תישלח" in sent[-1] and "#" not in sent[-1]
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
    mod._usage_cache.update(at=mod.time.time() + 3600, v={"five_hour": {"utilization": 27}, "seven_day": {"utilization": 70}})
    st = mod.StatusMsg("sonnet", wa=False)
    a = st._text()
    st.tick += 1
    b = st._text()
    st.finish()
    assert "\u23f3" in a and "\u231b" in b, "the hourglass alternates on every edit"
    host = mod.CFG.get("host_label") or mod.socket.gethostname()
    assert host in a.split("·")[0] and host in st._final().split("·")[0], "the computer is named first"
    assert "מגבלת" not in st._final() and "$" not in st._final(), "no usage/money line in the regular chat (owner 09.11 07:14)"
    mod._account_cache.update(at=mod.time.time() + 3600, v="me@example.com (Pro)")
    rep = mod.owner_command("/תקרה")
    ru = mod.restart_usage()
    assert "me@example.com" in rep and "me@example.com" in ru and "5 שעות 27%" in ru and "שבועית 70%" in ru and "מגבלת הגשר" in ru
    assert "27%" in rep and "70%" in rep and "כל הגשר" in rep and "השיחה הזו" in rep and "חודשי" not in rep


def test_downtime_notice_lists_messages_sent_while_down(agent, monkeypatch):
    mod, db = agent
    sent = []
    monkeypatch.setattr(mod, "reply", lambda text, **k: sent.append(text))
    cur = db.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]
    for i, t in enumerate(("01:02", "01:05")):
        db.execute("INSERT INTO messages VALUES (?, ?, 'me', 'שאלה', ?, 1, '', '')", (f"D{i}", mod.PRIMARY, f"2026-11-09 {t}:00+02:00"))
    db.commit()
    n = mod.downtime_notice(cur, mod.dt.datetime(2026, 11, 9, 1, 0))
    assert n == 2 and len(sent) == 1 and "01:02" in sent[0] and "01:05" in sent[0] and "01:00" in sent[0]


def test_rules_count(agent):
    mod, _ = agent
    assert mod.rules_count("# כותרת\n- א\n* ב\n1. ג\nטקסט חופשי\n-\n") == 3


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
    mod.CFG["name_prefix"] = "LP"
    mod.sync_group_name(mod.PRIMARY, "claude-opus-5-5")
    names = [b["name"] for p, b in calls if p == "/set_group"]
    assert names == ["LP \u05d2\u05e9\u05e8 \u05d0\u05d5\u05e4\u05d5\u05e1 \u05d5\u05d5\u05e6\u05d0\u05e4"], names  # גשר אופוס ווצאפ
    mod.sync_group_name(mod.PRIMARY, "opus")  # same name: no second rename
    assert len([1 for p, b in calls if p == "/set_group"]) == 1
    mod.kv_set(mod.K("name_set_at", mod.PRIMARY), "0")
    mod.sync_group_name(mod.PRIMARY, "sonnet")
    assert [b["name"] for p, b in calls if p == "/set_group"][-1].split()[2] == "\u05e1\u05d5\u05e0\u05d8"  # סונט


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


def test_group_name_single_source_and_owner_rename(agent, monkeypatch):
    mod, db = agent
    g = "120363000000000001@g.us"
    db.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    db.execute("INSERT INTO chats VALUES (?, ?, NULL)", (g, "גשר אופוס ויוי"))
    db.commit()
    open(mod.GROUPS_FILE, "w", encoding="utf-8").write(json.dumps({g: {"name": "שם ישן", "purpose": "dev"}}, ensure_ascii=False))
    mod._live_cache[0] = 0
    assert mod.load_groups()[g]["name"] == "גשר אופוס ויוי"  # every reader gets the WhatsApp name
    mod.sync_names_file()
    assert json.load(open(mod.GROUPS_FILE, encoding="utf-8"))[g]["name"] == "גשר אופוס ויוי"
    # only the model differs -> same name; a real rename -> model goes after the first word
    assert mod.name_core("גשר סונט ויוי") == mod.name_core("גשר {model} ויוי")
    assert mod.name_core("אוטומציה 4M-D · הייקו") == mod.name_core("אוטומציה 4M-D · {model}")
    mod.CFG["name_prefix"] = "LP"  # owner 09.11: computer prefix first, model after the first word, for every group
    assert mod.template_after_first_word("פיתוח הכלי שלי") == "LP פיתוח {model} הכלי שלי"
    assert mod.template_after_first_word("LP גשר אופוס דב") == "LP גשר {model} דב"
    assert mod.template_after_first_word("אוטומציה 4M-D · {model}") == "LP אוטומציה {model} 4M-D"
    assert mod.name_core("LP גשר אופוס דב") == "גשר דב"
    sent = []
    monkeypatch.setattr(mod, "bridge", lambda path, body, timeout=0: sent.append(body) or {"success": True})
    mod.kv_set(mod.K("name_tpl", g), "גשר {model} ויוי")  # an old template (no prefix) is upgraded
    mod.kv_set(mod.K("name_set", g), "גשר אופוס ויוי")
    mod.sync_group_name(g, "opus")
    assert sent[-1]["name"] == "LP גשר אופוס ויוי"
    db.execute("UPDATE chats SET name = 'LP גשר אופוס ויוי' WHERE jid = ?", (g,))
    db.commit()
    mod._live_cache[0] = 0
    mod.kv_set(mod.K("name_set_at", g), "0")
    mod.sync_group_name(g, "opus")
    assert len(sent) == 1  # nothing changed, no rename
    db.execute("UPDATE chats SET name = 'ויוי פיתוח' WHERE jid = ?", (g,))
    db.commit()
    mod._live_cache[0] = 0
    mod.sync_group_name(g, "opus")
    assert sent[-1]["name"] == "LP ויוי אופוס פיתוח"
    assert mod.kv_get(mod.K("name_tpl", g)) == "LP ויוי {model} פיתוח"


def test_push_alerts_queue_merge_and_usage(agent, monkeypatch):
    mod, db = agent
    sent = []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, **k: sent.append((path, body)) or {"success": True, "id": "E1"})
    monkeypatch.setitem(mod.CFG, "push_quiet", "")  # independent of the clock (the nightly release runs at 23:30, in quiet hours)
    mod.push("*שלום*   עולם", "g1@g.us")
    mod.push("", "g1@g.us")  # empty -> nothing
    later = mod.time.time() + 120
    with mod.adb() as a:  # a running command holds its own conversation's push until the answer is out
        a.execute("INSERT INTO commands (msg_id, text, status, chat_jid) VALUES ('m', 't', 'running', 'g1@g.us')")
    mod.push_send_due(later)
    assert sent == []
    with mod.adb() as a:
        a.execute("UPDATE commands SET status = 'done'")
    mod.push_send_due(mod.time.time())  # just queued: still gathering
    assert sent == []
    mod.push_send_due(later)
    assert len(sent) == 1 and sent[0][0] == "/send_rich"
    b = sent[0][1]
    assert b["kind"] == "event" and b["jid"] == "g1@g.us" and b["name"] == "שלום עולם" and b["start"] > mod.time.time()
    mod.push_send_due(later)
    assert len(sent) == 1, "sent once"
    # pushes close together -> one notification
    mod.push("א", "g1@g.us")
    mod.push("ב", "g1@g.us")
    mod.push_send_due(later)
    assert len(sent) == 2 and sent[1][1]["name"].startswith("🔔 2 התראות") and sent[1][1]["text"] == "א\nב"
    # usage at 80%: once per window
    reset = (mod.dt.datetime.now(mod.dt.timezone.utc) + mod.dt.timedelta(hours=2)).replace(microsecond=0)
    jitter = iter(range(1, 50))  # the real meter returns resets_at a few hundred ms apart on every read (10.11 bug)
    monkeypatch.setattr(mod, "claude_usage", lambda: {"five_hour": {"utilization": 100, "resets_at": (reset + mod.dt.timedelta(microseconds=next(jitter) * 9000)).isoformat()}, "seven_day": {"utilization": 40}})
    for _ in range(4):
        mod.push_watch_usage()
    with mod.adb() as a:
        texts = [r[0] for r in a.execute("SELECT text FROM push_queue WHERE kind = 'usage'")]
    assert len(texts) == 1 and "100%" in texts[0] and "משתחרר ב-" in texts[0]
    # a PC alone on its WhatsApp account watches people itself; a sharer waits while the leader is up
    monkeypatch.setattr(mod, "own_account", lambda: "972500000000")
    mod.CFG["peers"] = [{"name": "pc2", "tunnel": "x"}]
    mod.CFG["auto_release"] = False
    assert mod.watches_people()
    mod.kv_set("peer_account:pc2", "972500000000")
    mod.kv_set("peerwatch:pc2", "{}")
    assert not mod.watches_people()
    mod.kv_set("peerwatch:pc2", '{"off_since": "2026-11-09T10:00:00"}')
    assert mod.watches_people()
    # quiet hours: only an urgent fault goes out, the rest waits for the morning
    mod.CFG["push_quiet"] = "23:00-07:00"
    night = mod.time.mktime(mod.dt.datetime(2026, 11, 10, 2, 0).timetuple())
    with mod.adb() as a:
        a.execute("DELETE FROM push_queue")
    mod.push("לילה", "g1@g.us", "done")
    mod.push("תקלה", "g2@g.us", "fault")
    with mod.adb() as a:
        a.execute("UPDATE push_queue SET created = '2026-11-10T01:00:00'")
    n = len(sent)
    mod.push_send_due(night)
    assert len(sent) == n + 1 and sent[-1][1]["name"] == "תקלה"
    assert mod.in_quiet_hours(mod.dt.datetime(2026, 11, 9, 23, 30)) and not mod.in_quiet_hours(mod.dt.datetime(2026, 11, 9, 7, 0))
    mod.CFG["push_alerts"] = False
    mod.push("x", "g1@g.us")
    with mod.adb() as a:
        assert not a.execute("SELECT 1 FROM push_queue WHERE text = 'x'").fetchone()


def test_auto_transcript_posts_in_same_chat_once(agent, monkeypatch):
    mod, db = agent
    calls = []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, **k: calls.append((path, body)) or {"success": True})
    monkeypatch.setattr(mod, "voice_enabled", lambda: True)
    monkeypatch.setattr(mod, "watches_people", lambda: True)
    monkeypatch.setattr(mod, "transcript_of", lambda cj, mid, sender, ts: "שלום יקיר")
    now = mod.dt.datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
    mod.auto_transcript("972500000001@s.whatsapp.net", "V1", "972500000001", now)
    mod.auto_transcript("972500000001@s.whatsapp.net", "V1", "972500000001", now)  # once per recording
    assert [c for c in calls if c[0] != "/react"] == [("/send_transcript", {"jid": "972500000001@s.whatsapp.net", "id": "V1", "text": "🤖 תמלול: שלום יקיר"})]
    old = (mod.dt.datetime.now().astimezone() - mod.dt.timedelta(hours=2)).isoformat(sep=" ", timespec="seconds")
    mod.auto_transcript("972500000001@s.whatsapp.net", "V2", "972500000001", old)  # an old recording from a sync: no
    assert len(calls) == 1
    monkeypatch.setattr(mod, "watches_people", lambda: False)  # the other PC on the same account does it
    mod.auto_transcript("972500000001@s.whatsapp.net", "V3", "972500000001", now)
    assert len(calls) == 1


def test_reaction_counts_as_an_answer(agent):
    mod, db = agent
    db.execute("CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    now = mod.dt.datetime.now().astimezone()
    ts = (now - mod.dt.timedelta(hours=1)).isoformat(sep=" ", timespec="seconds")
    add(db, "W1", ts, "אתה מגיע מחר?", chat="972500000009@s.whatsapp.net")
    _, waiting = mod.summary_extract(now - mod.dt.timedelta(days=1), now)
    assert len(waiting) == 1, "waits for an answer"
    mod.note_reaction("972500000009@s.whatsapp.net", "W1", "👍")
    _, waiting = mod.summary_extract(now - mod.dt.timedelta(days=1), now)
    assert waiting == [], "a 👍 on the last message is an answer"
    mod.note_reaction("972500000009@s.whatsapp.net", "W1", "")
    _, waiting = mod.summary_extract(now - mod.dt.timedelta(days=1), now)
    assert len(waiting) == 1, "a removed reaction is not an answer"


def test_wheel_on_someone_elses_message_reads_it_in_that_chat(agent, monkeypatch):
    mod, db = agent
    sent, made = [], []
    monkeypatch.setattr(mod, "tts_ogg", lambda text, path, **k: made.append(text) or path)
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None, read_aloud_of=None: sent.append((chat, quoted, read_aloud_of)))
    add(db, "T1", "2026-11-09 17:00:00+02:00", "אני מאחר בעשר דקות", chat="972500000009@s.whatsapp.net")
    mod.read_other("972500000009@s.whatsapp.net", "T1")
    assert made == ["אני מאחר בעשר דקות"] and sent == [("972500000009@s.whatsapp.net", "T1", "T1")]
    db.execute("INSERT INTO messages VALUES ('I1', '972500000009@s.whatsapp.net', 'x', '', '2026-11-09 17:01:00+02:00', 0, 'image', '')")
    db.commit()
    mod.read_other("972500000009@s.whatsapp.net", "I1")
    assert len(sent) == 1, "an image: nothing"


def test_wheel_on_a_voice_note_posts_its_transcript(agent, monkeypatch):
    mod, db = agent
    calls = []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, **k: calls.append((path, body)) or {"success": True})
    monkeypatch.setattr(mod, "transcript_of", lambda cj, mid, sender, ts: "אני בדרך")
    monkeypatch.setattr(mod, "tts_ogg", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no voice for a voice note")))
    db.execute("INSERT INTO messages VALUES ('A1', '972500000009@s.whatsapp.net', 'x', '', '2026-11-09 17:30:00+02:00', 0, 'audio', '')")
    db.commit()
    mod.read_other("972500000009@s.whatsapp.net", "A1")
    assert [c for c in calls if c[0] != "/react"] == [("/send_transcript", {"jid": "972500000009@s.whatsapp.net", "id": "A1", "text": "🤖 תמלול: אני בדרך"})]


def test_restart_reason_only_hebrew(agent):
    mod, _ = agent
    assert mod.restart_reason_he("/הפעל מחדש") == "/הפעל מחדש"
    assert mod.restart_reason_he("segment_d: listener + roles") == "עדכון הגדרות / קוד (הפרטים ביומן השינויים)"


# ---------- failover: the backup machine runs another machine's plugins while it is off ----------

def _fo_setup(mod, tmp_path, monkeypatch):
    """A fake peer on this machine: its 'remote' scripts run locally against tmp_path/peer (same layout as a real install)."""
    import subprocess as sp
    peer_root = tmp_path / "peer"
    (peer_root / "agent").mkdir(parents=True)
    pdb = sqlite3.connect(peer_root / "agent" / "agent.db")
    pdb.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)")
    pdb.executemany("INSERT INTO kv VALUES (?, ?)", [("segd:lunch_send:2026-11-10", "09:10"), ("day:fo_report", "2026-11-10"),
                                                     ("at:fo_every", "2026-11-10T10:00:00"), ("session_id", "theirs"),
                                                     ("day:backup", "2026-11-10")])
    pdb.commit()
    pdb.close()
    plugin = tmp_path / "fo_plugin.py"
    plugin.write_text("def setup(agent):\n    agent.daily('08:00', 'fo_report', lambda: None)\n"
                      "    agent.every(60, 'fo_every', lambda: None)\n    agent.background(lambda: None)\n", encoding="utf-8")
    plugdb = tmp_path / "plugin.db"
    x = sqlite3.connect(plugdb)
    x.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)")
    x.execute("INSERT INTO kv VALUES ('procurement_sent_date', '2026-11-10')")
    x.commit()
    x.close()
    peer = {"name": "לפטופ", "url": "http://127.0.0.1:1", "ssh": "4-MINE", "root": str(peer_root)}
    mod.CFG["peers"] = [peer]
    mod.CFG["failover"] = {"for": "לפטופ", "plugins": [str(plugin)], "plugin_dbs": [str(plugdb).replace("\\", "/")]}
    monkeypatch.setattr(mod, "FAILOVER_DIR", str(tmp_path / "failover"))
    calls = []

    def run_local(p, script, timeout=120):
        calls.append(script)
        r = sp.run([sys.executable, "-I", "-X", "utf8", "-"], input=script.encode("utf-8"), capture_output=True, timeout=60)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    monkeypatch.setattr(mod, "_remote_python", run_local)
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append((chat, text)) or True)
    monkeypatch.setattr(mod, "push", lambda *a, **k: None)
    return peer, peer_root, sent, calls


def test_failover_step_asks_once_per_outage_and_releases(agent):
    mod, _ = agent
    fc = {"after_minutes": 10, "approve_text": "מאשר לנייח", "self_name": "נייח", "plugins": ["x/segment_d_auto.py"]}
    t0 = mod.dt.datetime(2026, 11, 10, 7, 0)
    m = lambda n: t0 + mod.dt.timedelta(minutes=n)
    off = {"off_since": t0.isoformat(timespec="seconds")}
    st, acts = mod.failover_step({}, off, m(5), fc, "לפטופ")
    assert acts == [] and (st.get("state") or "idle") == "idle", "a short blip is not an outage"
    st, acts = mod.failover_step(st, off, m(11), fc, "לפטופ")
    assert st["state"] == "asked" and len(acts) == 1 and acts[0][0] == "ask"
    assert "מאשר לנייח" in acts[0][1] and "07:00" in acts[0][1] and "segment_d_auto" in acts[0][1] and "אין כאן עותק" in acts[0][1]
    st, acts = mod.failover_step(st, off, m(40), fc, "לפטופ")
    assert acts == [], "one question per outage"
    st2, acts = mod.failover_step(st, {}, m(45), fc, "לפטופ")
    assert st2 == {"state": "idle"} and acts[0][0] == "say" and "חזר" in acts[0][1], "back before approval: nothing moves"
    st, acts = mod.failover_step({"state": "active", "since": m(20).isoformat(), "handback_pending": True}, {}, m(90), fc, "לפטופ")
    assert st["state"] == "releasing" and acts[0][0] == "release" and "חזר" in acts[0][1]
    st, acts = mod.failover_step(st, {"off_since": m(100).isoformat()}, m(115), fc, "לפטופ")
    assert st["state"] == "asked" and acts[0][0] == "ask", "down again before the hand-back: asked again"
    st, acts = mod.failover_step(st, {}, m(120), fc, "לפטופ")
    assert st["state"] == "releasing" and acts == [], "the pending hand-back is finished when it is back"


def test_failover_full_cycle(agent, tmp_path, monkeypatch):
    mod, _ = agent
    peer, peer_root, sent, calls = _fo_setup(mod, tmp_path, monkeypatch)
    now = mod.dt.datetime.now()
    # 1. laptop up: its state is copied here
    mod.failover_tick(peer, "לפטופ", {})
    snap = mod._fo_snapshot("לפטופ")
    assert snap["kv"]["segd:lunch_send:2026-11-10"] == "09:10" and "session_id" not in snap["kv"]
    assert snap["pdb"][mod.CFG["failover"]["plugin_dbs"][0]]["procurement_sent_date"] == "2026-11-10"
    # 2. laptop down 12 minutes: one question in the control group
    off = {"off_since": (now - mod.dt.timedelta(minutes=12)).isoformat(timespec="seconds")}
    mod.kv_set("peerwatch:לפטופ", json.dumps(off))
    mod.failover_tick(peer, "לפטופ", off)
    mod.failover_tick(peer, "לפטופ", off)
    asks = [t for _, t in sent if "להעביר" in t]
    assert len(asks) == 1 and sent[0][0] == mod.PRIMARY and "המצב האחרון" in asks[0]
    # 3. someone else's message does nothing; the owner's exact phrase moves the plugins here
    later = (now + mod.dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S+02:00")
    mod.handle(("o1", mod.PRIMARY, "x", later, "מאשר לנייח", False, ""))
    assert mod.failover_state("לפטופ")["state"] == "asked"
    mod.handle(("o2", mod.PRIMARY, "me", later, " מאשר  לנייח ", True, ""))
    st = mod.failover_state("לפטופ")
    assert st["state"] == "active" and sorted(st["jobs"]) == ["fo_every", "fo_report"]
    assert "עברו לנייח" in sent[-1][1]
    assert mod.kv_get("segd:lunch_send:2026-11-10") == "09:10", "done-today flags imported"
    assert mod.kv_get("day:fo_report") == "2026-11-10" and mod.kv_get("day:backup") is None, "only the plugins' own job flags"
    assert any(k == "fo_report" for _, k, _ in mod._daily) and os.path.exists(mod._fo_hold_path("לפטופ"))
    with mod.adb() as db:
        assert db.execute("SELECT count(*) FROM commands").fetchone()[0] == 0, "the phrase is never a Claude run"
    # 4. a job ran here meanwhile; the laptop answers again -> restart requested (plugins cannot be stopped in place)
    mod.kv_set("segd:lunch_send:2026-11-11", "09:10")
    mod.kv_set("day:fo_report", "2026-11-11")
    mod.failover_tick(peer, "לפטופ", {})
    assert mod.failover_state("לפטופ")["state"] == "releasing" and os.path.exists(mod.RESTART_FILE)
    assert os.path.exists(mod._fo_hold_path("לפטופ")), "still held until the restart"
    # 5. the new process (plugins not loaded) hands the flags back and releases
    mod._failover_loaded.clear()
    mod.failover_tick(peer, "לפטופ", {})
    p = sqlite3.connect(peer_root / "agent" / "agent.db")
    got = dict(p.execute("SELECT k, v FROM kv"))
    p.close()
    assert got["segd:lunch_send:2026-11-11"] == "09:10" and got["day:fo_report"] == "2026-11-11" and got["session_id"] == "theirs"
    assert not os.path.exists(mod._fo_hold_path("לפטופ")) and mod.failover_state("לפטופ")["state"] == "idle"
    assert "חזרו אליו" in sent[-1][1]


def test_failover_resume_after_restart_and_holds(agent, tmp_path, monkeypatch):
    mod, _ = agent
    peer, peer_root, sent, calls = _fo_setup(mod, tmp_path, monkeypatch)
    mod.failover_save("לפטופ", {"state": "active", "jobs": ["fo_report"], "since": "2026-11-10T07:00:00"})
    mod.failover_resume()
    assert "לפטופ" in mod._failover_loaded and any(k == "fo_report" for _, k, _ in mod._daily)
    # the laptop's side: a hold file for this host on the peer = do not run own plugins
    assert mod.plugin_holds() == []
    hold = peer_root / "agent" / "failover"
    hold.mkdir(parents=True)
    me = os.environ.get("COMPUTERNAME") or mod.socket.gethostname()
    (hold / "hold-x.json").write_text(json.dumps({"for": "לפטופ", "host": "OTHER-PC"}), encoding="utf-8")
    assert mod.plugin_holds() == [], "a hold for another machine"
    (hold / "hold-y.json").write_text(json.dumps({"for": "לפטופ", "host": me}), encoding="utf-8")
    assert mod.plugin_holds() == ["לפטופ"]
    monkeypatch.setattr(mod, "_remote_python", lambda *a, **k: (255, "", "ssh: connect timed out"))
    assert mod.plugin_holds() is None, "peer unreachable = unknown"


def test_failover_phrase_swallowed_on_other_machines(agent):
    mod, _ = agent
    mod.CFG["peers"] = [{"name": "נייח", "tunnel": "ybhw"}]
    mod.CFG.pop("failover", None)
    assert mod.failover_approve("מאשר לנייח", mod.PRIMARY, "2026-11-10 08:00:00+02:00", True)
    assert not mod.failover_approve("מאשר לנייח", mod.PRIMARY, "2026-11-10 08:00:00+02:00", False)
    assert not mod.failover_approve("מאשר לנייח בבקשה", mod.PRIMARY, "2026-11-10 08:00:00+02:00", True)
    assert mod.plugin_hold_applies() is False, "no plugins of its own"
    mod.CFG["plugins"] = ["x.py"]
    assert mod.plugin_hold_applies() is True


def test_draft_copy_text_is_not_an_instruction(agent):
    mod, db = agent
    db.execute("CREATE TABLE IF NOT EXISTS drafts (id INTEGER PRIMARY KEY, recipient TEXT, name TEXT, message TEXT, media_path TEXT, "
               "created TEXT, status TEXT, card_id TEXT, quoted_id TEXT, kind TEXT, payload TEXT)")
    now = mod.dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.0000000+02:00")
    db.execute("INSERT INTO drafts (recipient, name, message, created, status) VALUES ('x', 'y', ?, ?, 'pending')", ("שלום ציון\nשורה שנייה", now))
    db.commit()
    assert mod.is_draft_copy("שלום ציון\nשורה שנייה")
    assert not mod.is_draft_copy("תעשה משהו אחר")


def test_failover_auto_takes_over_without_asking(agent, monkeypatch):
    mod, _ = agent
    said, acted = [], []
    mod.CFG["failover"] = {"for": "לפטופ", "plugins": ["x.py"], "auto": True}
    monkeypatch.setattr(mod, "_fo_say", lambda fc, t: said.append(t))
    monkeypatch.setattr(mod, "push", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_fo_snapshot", lambda name: None)
    monkeypatch.setattr(mod, "failover_activate", lambda name, fc, st, now=None: acted.append(name) or "✅ עברו")
    off = (mod.dt.datetime.now() - mod.dt.timedelta(minutes=30)).isoformat(timespec="seconds")
    mod.failover_tick({"name": "לפטופ"}, "לפטופ", {"off_since": off})
    assert acted == ["לפטופ"] and said and "לאישור" not in said[0]


def test_haiku_hebrew_aliases(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    for word in ("חייקו", "הייקו"):  # the owner types חייקו; both spellings mean haiku
        mod.set_override("auto")
        mod.owner_command(f"/מודל {word}")
        assert mod.get_override() == "haiku", word


def test_push_guard_keeps_alerts_in_balance(agent):
    mod, _ = agent
    now = mod.time.time()
    iso = lambda d: d.isoformat(timespec="seconds")
    ago = lambda mins: iso(mod.dt.datetime.fromtimestamp(now) - mod.dt.timedelta(minutes=mins))
    with mod.adb() as db:  # the 5-hour alert was already sent 10 minutes ago
        db.execute("INSERT INTO push_queue (created, chat, kind, text, status, sent) VALUES (?, 'g', 'usage', ?, 'sent', ?)",
                   (ago(10), "📊 Claude: חלון 5 השעות הגיע ל-85%", ago(10)))
    spec = [("📊 Claude: חלון 5 השעות הגיע ל-100%", "usage"),   # same alert, other number -> dropped
            ("ליאור מחכה לתשובה", "waiting"),
            ("ליאור מחכה לתשובה", "waiting")]                  # copy inside the batch -> dropped
    rows = []
    with mod.adb() as db:
        for text, kind in spec:
            rid = db.execute("INSERT INTO push_queue (created, chat, kind, text) VALUES (?, 'g', ?, ?)", (ago(0), kind, text)).lastrowid
            rows.append((rid, "g", text, ago(0), kind))
    allowed = mod.push_guard(rows, now)
    assert [r[0] for r in allowed] == [rows[1][0]]
    with mod.adb() as db:
        st = dict(db.execute("SELECT id, status FROM push_queue WHERE id IN (?, ?, ?)", [r[0] for r in rows]))
        notice = db.execute("SELECT text FROM push_queue WHERE kind = 'guard'").fetchone()
    assert st[rows[0][0]] == "suppressed" and st[rows[2][0]] == "suppressed" and notice
    # rate cap: 6 sent in the last hour -> new non-fault alerts are dropped, a fault still goes
    with mod.adb() as db:
        for i in range(6):
            db.execute("INSERT INTO push_queue (created, chat, kind, text, status, sent) VALUES (?, 'g', 'x', ?, 'sent', ?)",
                       (ago(5), f"שונה {chr(1488 + i)}", ago(5)))
    more = [(900, "g", "משהו חדש", ago(0), "done"), (901, "g", "תקלה חדשה", ago(0), "fault")]
    assert [r[0] for r in mod.push_guard(more, now)] == [901]


def test_dictionary_hints_only_matching_entries(agent, tmp_path, monkeypatch):
    mod, _ = agent
    f = tmp_path / "dictionary.md"
    f.write_text("# מילון\n- טייל סקייל / טייס קייל → Tailscale  # 10.11\n- תרמוקס → Termux\nשורה בלי חץ\n", encoding="utf-8")
    monkeypatch.setattr(mod, "DICT_FILE", str(f))
    h = mod.dictionary_hints("טייס קייל עובד, תבדוק")
    assert "Tailscale" in h and "Termux" not in h
    assert mod.dictionary_hints("שלום") == ""
    monkeypatch.setattr(mod, "DICT_FILE", str(tmp_path / "none.md"))
    assert mod.dictionary_hints("תרמוקס") == ""


def test_monthly_job_runs_once_on_its_day(agent, monkeypatch):
    mod, _ = agent
    import datetime as _dt
    import calendar
    calls, said = [], []
    monkeypatch.setattr(mod, "claude", lambda prompt, **k: calls.append(prompt) or "החשבון מוכן")
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: said.append(text))
    monkeypatch.setattr(mod, "send_outbox", lambda since: None)
    today = _dt.date.today()
    other = 1 if today.day != 1 else 2
    mod.CFG["monthly_jobs"] = [{"name": "חשבון ציפורה", "day": 31, "prompt": "הפק חשבון"},
                               {"name": "אחר", "day": other, "prompt": "לא היום"}]
    last = calendar.monthrange(today.year, today.month)[1]
    mod.monthly_jobs()
    mod.monthly_jobs()
    expected = 1 if today.day == last else 0  # day 31 = this month's last day
    assert calls.count("הפק חשבון") == expected and "לא היום" not in calls
    assert len(said) == expected


def test_search_command_answers_locally(agent):
    mod, db = agent
    db.execute("CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    db.execute("INSERT INTO chats VALUES ('g9@g.us', 'מפגש ציפורה', '2026-11-10')")
    db.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me) VALUES "
               "('a', 'g9@g.us', '972500000009', '4 קרח בבקשה', '2026-11-10 10:00:00+02:00', 0), "
               "('b', 'g9@g.us', 'me', '🤖 קרח נספר', '2026-11-10 11:00:00+02:00', 1)")
    db.commit()
    out = mod.owner_command("/חפש קרח")
    assert "מפגש ציפורה" in out and "4 קרח" in out and "נספר" not in out
    assert "כתוב מה לחפש" in mod.owner_command("/ חפש")
    assert "לא נמצא" in mod.owner_command("/חפש שוקולד")


def test_health_report_counts_downtime_and_failures(agent):
    mod, db = agent
    import datetime as _dt
    db.execute("CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS conn_log (at TIMESTAMP, event TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS send_log (id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT, message_id TEXT, ok BOOLEAN, detail TEXT, timestamp TIMESTAMP)")
    t = _dt.datetime.now() - _dt.timedelta(hours=3)
    f = lambda d: (t + _dt.timedelta(minutes=d)).strftime("%Y-%m-%d %H:%M:%S")
    db.executemany("INSERT INTO conn_log VALUES (?, ?)", [(f(0), "disconnected"), (f(10), "connected"), (f(30), "disconnected"), (f(32), "connected")])
    db.executemany("INSERT INTO send_log (recipient, message_id, ok, detail, timestamp) VALUES (?, ?, ?, ?, ?)",
                   [("x@s.whatsapp.net", "1", 1, "", f(1)), ("y@s.whatsapp.net", "2", 0, "timeout", f(2))])
    db.commit()
    out = mod.health_report()
    assert "ניתוקים:* 2" in out and "סה\"כ 12 דק'" in out and "הארוך ביותר 10 דק'" in out
    assert "נכשלו:* 1" in out and "timeout" in out and "מחכים" not in out


def test_once_job_runs_once_after_its_time(agent, monkeypatch):
    mod, _ = agent
    calls = []
    monkeypatch.setattr(mod, "claude", lambda prompt, **k: calls.append(prompt) or "בוצע")
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: None)
    monkeypatch.setattr(mod, "send_outbox", lambda since: None)
    mod.CFG["once_jobs"] = [{"name": "לילה", "at": "2020-01-01 03:00", "prompt": "עבודת לילה"},
                            {"name": "עתיד", "at": "2999-01-01 03:00", "prompt": "לא עכשיו"}]
    mod.once_jobs()
    mod.once_jobs()
    assert calls == ["עבודת לילה"]


GUEST = "1203638888@g.us"


@pytest.fixture()
def guest(agent, tmp_path, monkeypatch):
    mod, db = agent
    gf = tmp_path / "groups.json"
    gf.write_text(json.dumps({GUEST: {"name": "ריטה", "purpose": "guest", "guest": True, "home": str(tmp_path / "rita"),
                                      "guests": ["972501111111", "238800000000"], "model": "haiku", "speaker": "ריטה"},
                              DEV: {"name": "פיתוח", "purpose": "dev"}}), encoding="utf-8")
    monkeypatch.setattr(mod, "GROUPS_FILE", str(gf))
    return mod, db, tmp_path


def test_guest_group_isolation(guest, monkeypatch):
    mod, _, tmp = guest
    assert mod.guest_cfg(GUEST) and not mod.guest_cfg(DEV) and not mod.guest_cfg(mod.PRIMARY)
    assert mod.is_guest_sender(GUEST, "972501111111") and mod.is_guest_sender(GUEST, "238800000000@lid")
    assert not mod.is_guest_sender(GUEST, "972509999999") and not mod.is_guest_sender(DEV, "972501111111")
    assert mod.GUEST_CMDS.match("/מודל סונט") and mod.GUEST_CMDS.match("/🛞") and mod.GUEST_CMDS.match("/סשן")
    assert not mod.GUEST_CMDS.match("/חפש יקיר") and not mod.GUEST_CMDS.match("/הפעל מחדש") and not mod.GUEST_CMDS.match("/תקרה")
    assert mod.pick_model_info("שלום", GUEST) == ("haiku", "forced")
    assert mod.handoff_path(GUEST).startswith(str(tmp / "rita"))
    seen = {}

    class P:
        def __init__(self, cmd, cwd=None, **kw):
            seen["cmd"], seen["cwd"] = cmd, cwd
            raise RuntimeError("stop")
    monkeypatch.setattr(mod.subprocess, "Popen", P)
    monkeypatch.setitem(mod.CFG, "chrome", True)
    monkeypatch.setitem(mod.CFG, "permission_mode", "bypassPermissions")
    with pytest.raises(RuntimeError):
        mod._run_claude("hi", "haiku", None, 10, chat=GUEST)
    c = seen["cmd"]
    assert seen["cwd"] == str(tmp / "rita") and "--restricted" in c and "--safe-mode" in c and "--strict-mcp-config" in c
    assert c[c.index("--tools") + 1] == mod.GUEST_TOOLS and "Bash" not in mod.GUEST_TOOLS
    assert c[c.index("--permission-mode") + 1] == "bypassPermissions" and "--chrome" not in c
    with pytest.raises(RuntimeError):
        mod._run_claude("hi", "haiku", None, 10, chat=DEV)
    assert seen["cwd"] == mod.HOME and "--restricted" not in seen["cmd"] and "--chrome" in seen["cmd"]


def test_no_time_cap_by_default(agent, monkeypatch):
    """Owner 11.11: no time cap on a command unless config claude_timeout_minutes > 0; per-group values are ignored."""
    agent, _ = agent
    monkeypatch.setattr(agent, "load_groups", lambda: {"g": {"timeout_minutes": 60}})
    monkeypatch.setitem(agent.CFG, "claude_timeout_minutes", 0)
    assert agent.run_timeout_minutes("g") is None
    monkeypatch.setitem(agent.CFG, "claude_timeout_minutes", 30)
    assert agent.run_timeout_minutes("g") == 30


def test_dashboard_recording_is_transcribed(agent, monkeypatch, tmp_path):
    """Owner 11.11: the dashboard's 🎤 recording (an attached .webm) becomes the spoken text; other files stay as they are."""
    agent, _ = agent
    f = tmp_path / "voice.webm"
    f.write_bytes(b"x")
    monkeypatch.setattr(agent, "voice_enabled", lambda: True)
    monkeypatch.setattr(agent, "transcribe", lambda p: "שלום עולם")
    out = agent.pc_voice_to_text(f"[קובץ מצורף: {f}]")
    assert out.startswith("🎤 שלום עולם\n") and str(f) in out
    assert agent.pc_voice_to_text("[קובץ מצורף: C:/x/a.pdf]") == "[קובץ מצורף: C:/x/a.pdf]"


def test_edit_pairs_learns_word_swaps(agent):
    mod, _ = agent
    assert mod.edit_pairs("תבדוק את האפשיט של הרכבים", "תבדוק את ה-AppSheet של הרכבים") == [("האפשיט", "ה-AppSheet")]
    assert mod.edit_pairs("חמה של נדב תוקן", "המה של נדב תוקן") == [("חמה", "המה")]
    assert mod.edit_pairs("שלח ב 9:15", "שלח ב 9:20") == []          # numbers are not vocabulary
    assert mod.edit_pairs("אותו דבר", "אותו דבר") == []


def test_bridge_rules_in_every_conversation(agent, monkeypatch):
    """Owner 11.11: the bridge-wide rules (home/bridge_rules.md) reach every conversation's prompt."""
    agent, _ = agent
    os.makedirs(agent.HOME, exist_ok=True)  # home/ is personal and not in git: a clean checkout (CI) has no such folder
    with open(os.path.join(agent.HOME, agent.BRIDGE_RULES), "w", encoding="utf-8") as f:
        f.write("- כלל לדוגמה")
    assert "כלל לדוגמה" in agent.group_prompt(agent.PRIMARY)
    monkeypatch.setattr(agent, "load_groups", lambda: {"x@g.us": {"name": "x", "purpose": "dev"}})
    assert "כלל לדוגמה" in agent.group_prompt("x@g.us")


def test_weekly_limit_uses_meter_reset_and_bridge_text(agent, monkeypatch):
    """Owner 13.11: a full weekly window must wait for its real reset (days) and the bridge words the status itself."""
    mod, _ = agent
    soon = (mod.dt.datetime.now(mod.dt.timezone.utc) + mod.dt.timedelta(days=1, hours=3)).isoformat()
    monkeypatch.setattr(mod, "claude_usage", lambda max_age=120: {"five_hour": {"utilization": 0, "resets_at": soon},
                                                                 "seven_day": {"utilization": 100, "resets_at": soon}})
    reset = mod.meter_reset("You've hit your weekly limit")
    assert reset and (reset - mod.dt.datetime.now()).total_seconds() > 24 * 3600
    txt = mod.limit_status_text(reset)
    assert "המגבלה השבועית" in txt and "שבועית 100%" in txt and "5 שעות 0%" in txt and "הודעה מהגשר" in txt
    assert "חודשית" not in txt and "ממתינות לטיפול: *0*" in txt, "no data from Claude = not mentioned; waiting count shown"
    monkeypatch.setattr(mod, "claude_usage", lambda max_age=120: None)
    assert mod.meter_reset("hit your limit") is None


def test_own_voice_echo_is_not_a_command(agent):
    """Owner 13.11: the 🛞 reading we sent came back as the owner's audio message and was answered as if he had spoken."""
    mod, db = agent
    db.execute("ALTER TABLE messages ADD COLUMN file_length INTEGER")
    db.execute("INSERT INTO messages (id, chat_jid, sender, content, timestamp, is_from_me, media_type, file_length) "
               "VALUES ('E1', 'g@g.us', 'x', '', '2026-11-13 10:12:53', 1, 'audio', 715227)")
    db.commit()
    assert not mod.is_own_voice_echo("g@g.us", "E1", 1), "nothing sent yet"
    mod.kv_set("own_voice", json.dumps({"t": time.time(), "size": 724114}))
    assert mod.is_own_voice_echo("g@g.us", "E1", 1)
    assert not mod.is_own_voice_echo("g@g.us", "E1", 0), "someone else's audio is never the echo"
    mod.kv_set("own_voice", json.dumps({"t": time.time(), "size": 120000}))
    assert not mod.is_own_voice_echo("g@g.us", "E1", 1), "a different size = the owner's own voice note"
    mod.kv_set("own_voice", json.dumps({"t": time.time() - 600, "size": 724114}))
    assert not mod.is_own_voice_echo("g@g.us", "E1", 1), "too old"


def test_wheel_busy_flips_hourglass_and_restores_wheel(agent, monkeypatch):
    """Owner 13.11: ⏳/⌛ flip while the 🛞 work runs, then the 🛞 is put back; that restored 🛞 must not restart the work."""
    mod, db = agent
    calls = []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, timeout=30, **k: calls.append((path, body["emoji"])) or {})
    monkeypatch.setattr(mod, "pc_only", lambda: False)
    with mod.WheelBusy("g@g.us", "M1", every=0.05):
        time.sleep(0.3)
    emojis = [e for _, e in calls]
    assert "⏳" in emojis and "⌛" in emojis and emojis[-1] == "🛞" and all(p == "/react" for p, _ in calls)
    assert mod.wheel_is_ours("g@g.us", "M1") and not mod.wheel_is_ours("g@g.us", "OTHER")


def test_live_control_stop_and_queue(agent, monkeypatch):
    """Owner 13.11: /עצור (Esc), /עצור <instruction>, /עצור הכל (Ctrl+C), /תור — answered at once, never queued behind the work."""
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None, **k: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    monkeypatch.setattr(mod, "STOP_FILE", str(mod.os.path.join(mod.os.path.dirname(mod.AGENT_DB), "stop.request")))
    chat = mod.PRIMARY
    for i in (1, 2):
        mod.enqueue_command(f"Q{i}", f"משימה {i}", "2026-11-13 10:00", chat)
    assert not mod.live_control(chat, "X0", "שלום") and not mod.live_control(chat, "X0", "/מודל אופוס")
    assert mod.live_control(chat, "X1", "/תור") and "בתור" in sent[-1] and "משימה 2" in sent[-1]
    mod._active_runs[chat] = 1.0
    try:
        assert mod.live_control(chat, "X2", "/עצור") and "עוצר" in sent[-1] and "2 הודעות" in sent[-1]
        assert open(mod.STOP_FILE, encoding="utf-8").read() == chat
        assert mod.live_control(chat, "X3", "/עצור תעשה רק את הראשון") and "אחרי 2 הודעות" in sent[-1]
        assert mod.live_control(chat, "X4", "/עצור הכל") and "בוטלו 3" in sent[-1]
    finally:
        mod._active_runs.pop(chat, None)
    with mod.adb() as db:
        assert [r[0] for r in db.execute("SELECT status FROM commands ORDER BY id")] == ["cancelled"] * 3
    assert mod.live_control(chat, "X5", "/stop") and "אין פעולה שרצה" in sent[-1]


def test_stop_and_switch_model_continues(agent, monkeypatch, tmp_path):
    """Owner 13.11: an order given on the wrong model — '/עצור אופוס' stops, switches, and the same work goes on."""
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None, **k: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a: None)
    monkeypatch.setattr(mod, "STOP_FILE", str(tmp_path / "stop.request"))
    monkeypatch.setattr(mod, "sync_group_name", lambda *a: None, raising=False)
    chat = mod.PRIMARY
    mod._active_runs[chat] = 1.0
    try:
        assert mod.live_control(chat, "S1", "/עצור אופוס")
    finally:
        mod._active_runs.pop(chat, None)
    assert mod.get_override(chat) == "opus" and ("אופוס" in sent[-1] or "Opus" in sent[-1])
    with mod.adb() as db:
        texts = [r[0] for r in db.execute("SELECT text FROM commands WHERE status = 'pending'")]
    assert len(texts) == 1 and "המשך" in texts[0]


def test_provider_layer(agent, monkeypatch):
    """Owner 13.11: the bridge's provider (not a group's) — another model server behind the same engine; the subscription
    limit binds only while the subscription is the provider."""
    mod, _ = agent
    assert mod.active_provider() == ("claude", None) and mod.provider_env() == (None, None)
    env, m = mod.provider_env("ollama")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434" and env["ANTHROPIC_API_KEY"] == "" and m == "bridge-qwen3-4b:latest"
    assert env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == "bridge-qwen3-4b:latest"
    mod.kv_set("limit_until", (mod.dt.datetime.now() + mod.dt.timedelta(hours=5)).isoformat(timespec="seconds"))
    assert mod.limit_until()
    monkeypatch.setattr(mod, "provider_check", lambda n: {"ok": n != "openrouter", "detail": "x", "models": ["bridge-qwen3-4b", "qwen3:4b-instruct"]})
    assert "לא הוחלף" in mod.owner_command("/ספק openrouter", mod.PRIMARY)
    assert "לא נמצא" in mod.owner_command("/ספק ollama nosuch", mod.PRIMARY)
    assert "✅" in mod.owner_command("/ספק אולמה qwen3:4b-instruct", mod.PRIMARY)
    assert mod.active_provider() == ("ollama", "qwen3:4b-instruct") and mod.limit_until() is None, "no subscription limit on Ollama"
    assert mod.provider_env()[1] == "qwen3:4b-instruct"
    mod.owner_command("/ספק claude", mod.PRIMARY)
    assert mod.active_provider()[0] == "claude" and mod.limit_until()


def test_auth_regex_and_login_code(agent, monkeypatch):
    mod, _ = agent
    # owner 13.11: the real expiry text from Claude Code must count as a sign-out
    assert mod.AUTH_RE.search("Failed to authenticate: OAuth session expired and could not be refreshed")
    assert not mod.AUTH_RE.search("all good")
    # the message carries the link and the code instruction
    monkeypatch.setattr(mod, "login_link", lambda: "https://claude.com/cai/oauth/authorize?x=1")
    msg = mod.auth_msg()
    assert "https://claude.com/cai/oauth/authorize?x=1" in msg and "קוד" in msg
    # no link available: the old one-line message
    monkeypatch.setattr(mod, "login_link", lambda: "")
    assert mod.auth_msg() == mod.AUTH_MSG
    # a code with no open sign-in is not ours
    mod._login["proc"] = None
    assert mod.login_code_reply("קוד abcdefghijkl") is None


def test_ollama_hosts_laptop_default_desktop_fallback(agent, monkeypatch):
    """Owner 13.11: Ollama on both PCs — the first host that answers (and has the model) serves; the next is the fallback."""
    mod, _ = agent
    up = {"http://127.0.0.1:11435": ["bridge-qwen3-4b:latest"], "http://127.0.0.1:11434": ["bridge-qwen3-4b:latest", "other"]}

    class Resp:
        def __init__(self, names): self.names = names
        def read(self): return json.dumps({"models": [{"name": n} for n in self.names]}).encode()

    def fake_urlopen(req, timeout=0):
        url = req if isinstance(req, str) else req.full_url
        base = url.rsplit("/api/tags", 1)[0]
        if base not in up:
            raise OSError("down")
        return Resp(up[base])
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setitem(mod.CFG, "providers", {"ollama": {"hosts": [{"url": "http://127.0.0.1:11435", "label": "לפטופ"},
                                                                  {"url": "http://127.0.0.1:11434", "label": "נייח"}]}})
    env, m = mod.provider_env("ollama")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11435" and m == "bridge-qwen3-4b:latest", "the laptop is the default"
    c = mod.provider_check("ollama")
    assert c["ok"] and "לפטופ ✅" in c["detail"] and "נייח ✅" in c["detail"]
    del up["http://127.0.0.1:11435"]
    mod._host_cache.clear()
    env, _ = mod.provider_env("ollama")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434", "laptop down -> the desktop's Ollama"
    assert "לפטופ ⚪" in mod.provider_check("ollama")["detail"] and "עובד דרך נייח" in mod.provider_check("ollama")["detail"]
    up.clear()
    mod._host_cache.clear()
    assert not mod.provider_check("ollama")["ok"]


def test_conflict_merge_goes_to_another_provider_when_claude_is_limited(agent, monkeypatch):
    mod, _ = agent
    calls = []
    monkeypatch.setattr(mod, "limit_until", lambda: "05:00")  # the subscription's usage limit is on
    monkeypatch.setattr(mod, "provider_env", lambda name=None, model=None: (None, None) if name is None
                        else ({"ANTHROPIC_BASE_URL": "https://p"}, "deepseek-chat"))
    monkeypatch.setattr(mod, "fallback_providers", lambda skip=None: [("deepseek", "deepseek-chat")])

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **k: calls.append((cmd, k.get("env"))) or R())
    assert mod._resolve_conflicts(["a.py"]) == ""
    assert len(calls) == 1 and calls[0][1] == {"ANTHROPIC_BASE_URL": "https://p"} and "deepseek-chat" in calls[0][0]
    # nothing else answers: the failure is reported, the merge is aborted by the caller
    monkeypatch.setattr(mod, "fallback_providers", lambda skip=None: [])
    assert mod._resolve_conflicts(["a.py"]) == "usage limit"


def test_switch_words_and_the_answer_tag(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "PROVIDER_FILE", str(tmp_path / "provider.json"))
    assert mod.resolve_ai_word("דיפסיק") == ("deepseek", None) and mod.resolve_ai_word("OpenAI") == ("openai", None)
    assert mod.resolve_ai_word("qwencode") == ("ollama", "qwen2.5-coder:3b") and mod.resolve_ai_word("קלוד") == ("claude", None)
    assert mod.resolve_ai_word("nope") is None and mod.resolve_ai_word("סשן") is None  # a real command is never a switch word
    monkeypatch.setattr(mod, "provider_check", lambda n: {"ok": False, "detail": "אין מפתח", "models": []})
    assert "לא מחובר" in mod.owner_command("/דיפסיק") and mod.active_provider()[0] == "claude"  # not connected: nothing switches
    monkeypatch.setattr(mod, "provider_check", lambda n: {"ok": True, "detail": "", "models": ["qwen2.5-coder:3b", "qwen3:8b"]})
    assert "ספק הגשר" in mod.owner_command("/qwencode") and mod.active_provider() == ("ollama", "qwen2.5-coder:3b")
    tag = mod.ai_tag("opus")
    assert tag.startswith("🧠 Ollama") and "qwen2.5-coder:3b" in tag and tag.endswith("\n")
    assert "ספק הגשר" in mod.owner_command("/ספק ollama qwen3:8b") and mod.active_provider() == ("ollama", "qwen3:8b")
    assert "לא נמצא" in mod.owner_command("/ספק ollama no-such-model")
    mod.owner_command("/קלוד")
    assert mod.ai_tag("opus").startswith("🧠 Claude")
    monkeypatch.setitem(mod.CFG, "show_ai_tag", False)
    assert mod.ai_tag("opus") == ""

