"""Owner 20.11 11:30: context meter, rotation by context size, rules by topic, fixed instructions once per session."""
import json

from test_agent import agent  # noqa: F401


def test_meter_claude_exact_with_cache_and_saved(agent):
    mod, _ = agent
    c = mod.PRIMARY
    res = {"session_id": "s1", "_last_usage": {"input_tokens": 10, "cache_read_input_tokens": 40000, "cache_creation_input_tokens": 990},
           "usage": {"input_tokens": 20, "cache_read_input_tokens": 90000, "cache_creation_input_tokens": 9980, "output_tokens": 500}}
    m = mod.ctx_meter_record(c, "claude", "sonnet", res, 3000, 300, 6000)
    assert m["exact"] and m["ctx"] == 41000 and m["window"] == 200_000 and m["cache"] == 90 and m["saved"] == 2000
    line = mod.ctx_meter_line(c)
    assert line.startswith("🧮 *הקשר:* 41K / 200K (20%)") and "💾 מטמון 90%" in line and "✂️ נחסכו ~2K" in line


def test_meter_estimates_for_a_provider_without_usage(agent, monkeypatch):
    mod, _ = agent
    c = mod.PRIMARY
    monkeypatch.setattr(mod, "providers", lambda: {"gemini_cli": {"kind": "cli"}})
    mod.ctx_meter_record(c, "gemini_cli", "", {"result": "x", "usage": {}}, 9000, 0, 0)
    line = mod.ctx_meter_line(c)
    assert line == "🧮 *הקשר:* ~3K (הערכה)"
    assert mod.ctx_meter_line(c, since=mod.time.time() + 60) == ""  # an older reading is not shown on a new run


def test_session_rotates_when_the_context_is_big(agent):
    mod, _ = agent
    c = mod.PRIMARY
    mod.kv_set(mod.K("session_id", c), "big")
    mod.kv_set(mod.K("session_started", c), mod.dt.datetime.now().isoformat())
    mod.kv_set(mod.K("session_runs", c), "1")
    mod.CFG["session_max_ctx_tokens"] = 100_000
    mod.kv_set(mod.K("ctx_meter", c), json.dumps({"exact": True, "sid": "big", "ctx": 90_000, "window": 200_000}))
    assert not mod.session_rotates(c, "שלום")
    mod.kv_set(mod.K("ctx_meter", c), json.dumps({"exact": True, "sid": "big", "ctx": 130_000, "window": 200_000}))
    assert mod.session_rotates(c, "שלום")
    mod.kv_set(mod.K("ctx_meter", c), json.dumps({"exact": True, "sid": "other", "ctx": 130_000, "window": 200_000}))
    assert not mod.session_rotates(c, "שלום")  # a reading of another session does not count


def test_rules_are_cut_to_the_conversation_topics(agent, monkeypatch, tmp_path):
    mod, _ = agent
    txt = ("- *עברית תמיד (יקיר, לכל הגשר):* תמיד עברית, גם בדוחות מקטע D\n"
           "- *שם דוח מקטע D (יקיר):* שם הדוח\n  שורת המשך\n"
           "- *משיכה מ-hub לפני כל שינוי קוד (יקיר):* git pull\n")
    entries = mod.rule_entries(txt)
    assert len(entries) == 3 and "שורת המשך" in entries[1]
    assert mod.rule_topics(entries[0]) == set()  # the body mentions 4M work, the title does not: a rule for everyone
    assert mod.rule_topics(entries[1]) == {"4m"} and mod.rule_topics(entries[2]) == {"dev"}
    gf = mod.os.path.join(str(tmp_path), "groups.json")
    open(gf, "w", encoding="utf-8").write(json.dumps({"g@g.us": {"purpose": "dev"}, "m@g.us": {"topics": ["4m"]}, "a@g.us": {}}))
    monkeypatch.setattr(mod, "GROUPS_FILE", gf)
    assert mod.chat_topics("g@g.us") == {"dev"} and mod.chat_topics("m@g.us") == {"4m"} and mod.chat_topics("a@g.us") is None


def test_fixed_reply_instructions_split_from_the_message(agent):
    mod, _ = agent
    full = mod.T["prompt"].format(owner="יקיר", when="10:00", text="שורה א\n\nשורה ב", where="בקבוצה", dest="לקבוצה")
    head, _, rules = full.rpartition("\n\n")
    assert head.endswith("שורה א\n\nשורה ב") and rules.startswith(("ענה בשפה", "Answer briefly"))
