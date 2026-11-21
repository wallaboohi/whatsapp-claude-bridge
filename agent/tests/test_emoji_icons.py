import time
"""🔥 = emoji icons in the owner's own message (owner 18.11): the table / stemming module and the agent's reaction handling."""
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import emoji_icons as ei  # noqa: E402
from test_agent import agent  # noqa: E402,F401  (the agent fixture)


def strip_added(text):
    return "".join(c for c in text if not ei._EMOJI_RE.match(c) or c.isdigit() or c in "#*").replace("  ", " ")


def test_owner_example_exactly():
    assert ei.iconize("מחר ב-8 פגישה עם הספק על המשאית", bold=False) == "מחר ב-8 ⏰ פגישה 🤝 עם הספק 🏭 על המשאית 🚚"
    # owner 19.11: the important word (the time) is also bold
    assert ei.iconize("מחר ב-8 פגישה עם הספק על המשאית") == "מחר *ב-8* ⏰ פגישה 🤝 עם הספק 🏭 על המשאית 🚚"


def test_cldr_layer_reaches_almost_every_emoji():
    """Owner 19.11 11:23: a context for all the icons, also those the hand table does not use."""
    d = json.load(open(os.path.join(HERE, "emoji_he.json"), encoding="utf-8"))
    assert d["covered"] >= 1900
    assert ei.lookup("קרח")[0][0] == "🧊" and ei.lookup("לישראל")[0][0] == "🇮🇱" and ei.lookup("פיצה")[0][0] == "🍕"
    assert ei.lookup("משאית")[1] != "cldr"  # the hand table stays first


def test_bold_is_light_and_idempotent():
    t = "הגענו עם הכלב בשעה 14:00 ושילמנו 300₪ על הכל"
    out = ei.iconize(t)
    assert "*14:00*" in out and out.count("*") <= 8
    assert ei.iconize(out) == out
    assert ei.iconize("*דחוף* מחר בבוקר רופא").startswith("*דחוף*")  # existing formatting untouched
    assert "*" not in ei.iconize("```מחר ב-8 פגישה עם הספק```").replace("```", "")
    assert ei.iconize("מחר ב-8") .count("*") == 0  # too short to bold


def test_prefixes_plurals_and_construct_forms():
    for word, emoji in (("והמשאיות", "🚚"), ("לגדר", "🚧"), ("בקסדות", "⛑️"), ("שבמחסן", "🏭"), ("ארוחת", "🍽️"), ("העובדים", "👷")):
        assert ei.lookup(word) and ei.lookup(word)[0][0] == emoji, word


def test_short_stems_do_not_misfire():
    assert ei.lookup("בקר") is None and ei.lookup("האם") is None
    assert ei.lookup("שקר")[0][0] == "🤥"  # a word of its own (CLDR), never "קר" -> cold
    assert ei.lookup("הים")[0][0] == "🌊"


def test_text_and_order_kept_and_no_repeats():
    t = "תזמין 3 משאיות לגדר, והקסדות הגיעו למחסן.\n*דחוף* לבדוק את המצלמות והמשאית בשער"
    out = ei.iconize(t, bold=False)
    assert out != t
    added = [w for w in out.split() if ei.is_emoji(w)]
    assert len(added) == len(set(added))  # the same emoji never twice in one message
    import re
    assert re.sub(r" [^\sA-Za-z֐-׿\d.,:;!?*_~]+", "", out) == t  # only " <emoji>" was inserted
    assert "לגדר 🚧," in out and "*דחוף* " in out  # before punctuation, after the closing bold mark


def test_density_and_nothing_fits():
    out = ei.iconize("משאית רכב אוטובוס מונית אופנוע רכבת מטוס מסוק")
    assert sum(1 for w in out.split() if ei.is_emoji(w)) <= 4
    assert ei.iconize("את זה של מי") == "את זה של מי"


def test_twice_adds_nothing_twice():
    once = ei.iconize("מחר ב-8 פגישה עם הספק על המשאית")
    assert ei.iconize(once) == once


def test_phrases_and_times():
    assert "ארוחת בוקר 🍳" in ei.iconize("ארוחת בוקר בשעה 7:30")
    assert "*7:30* ⏰" in ei.iconize("ארוחת בוקר בשעה 7:30")
    assert "ב-18.11 📅" in ei.iconize("ב-18.11 נסיעה")


def test_ai_fallback_only_adds_valid_emoji():
    seen = {}

    def ai(prompt):
        seen["p"] = prompt
        return 'here: {"0": "🦒", "2": "not emoji", "4": "🦒", "9": "🐙"}'
    out = ei.iconize("ג'ירפה נחמדה מאוד בגן החיות", ai=ai)
    assert out.startswith("ג'ירפה 🦒 ") and out.count("🦒") == 1 and "🐙" not in out and "not" not in out
    assert ei.iconize("זלמנובסקי נחמדה", ai=lambda p: 1 / 0) == "זלמנובסקי נחמדה"  # a broken AI = the table result


def test_table_is_large_and_valid():
    assert len(ei.WORDS) > 800
    for w, (emojis, _, _) in ei.WORDS.items():
        assert emojis and all(ei.is_emoji(e) for e in emojis), w


def _own(db, mid, chat, content, minutes_ago, media=""):
    ts = (dt.datetime.now().astimezone() - dt.timedelta(minutes=minutes_ago)).isoformat(sep=" ", timespec="seconds")
    db.execute("INSERT INTO messages VALUES (?, ?, 'me', ?, ?, 1, ?, '')", (mid, chat, content, ts, media))
    db.commit()


def test_fire_edits_a_fresh_own_message(agent, monkeypatch):
    mod, db = agent
    calls, sent = [], []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, timeout=180, tok=None: calls.append((path, body)) or {"success": True})
    monkeypatch.setattr(mod, "send", lambda *a, **k: sent.append((a, k)) or True)
    monkeypatch.setattr(mod, "fire_ai", lambda p: "")
    chat = "972500000001@s.whatsapp.net"
    _own(db, "F1", chat, "מחר ב-8 פגישה עם הספק על המשאית", 2)
    mod.fire_icons(chat, "F1")
    assert calls == [("/edit_message", {"jid": chat, "id": "F1", "text": "מחר *ב-8* ⏰ פגישה 🤝 עם הספק 🏭 על המשאית 🚚"})]
    assert not sent
    assert mod.fire_is_ours(chat, "F1")


def test_fire_old_message_is_answered_not_edited(agent, monkeypatch):
    mod, db = agent
    calls, sent = [], []
    monkeypatch.setattr(mod, "bridge", lambda path, body=None, timeout=180, tok=None: calls.append(path) or {"success": True})
    monkeypatch.setattr(mod, "send", lambda *a, **k: sent.append((a, k)) or True)
    monkeypatch.setattr(mod, "fire_ai", lambda p: "")
    monkeypatch.setattr(mod, "chat_title", lambda c: "דני")
    other = "972500000001@s.whatsapp.net"
    _own(db, "F2", other, "פגישה עם הספק", 40)
    mod.fire_icons(other, "F2")
    assert not calls and sent[0][0][0] == mod.PRIMARY and "*פגישה* 🤝 עם הספק 🏭" in sent[0][0][1]  # never to the other person
    _own(db, "F3", mod.PRIMARY, "פגישה עם הספק", 40)
    mod.fire_icons(mod.PRIMARY, "F3")
    assert sent[1][0][0] == mod.PRIMARY and sent[1][1].get("quoted") == "F3"


def test_fire_ignores_others_media_and_bot_answers(agent, monkeypatch):
    mod, db = agent
    calls = []
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: calls.append(a) or {"success": True})
    monkeypatch.setattr(mod, "send", lambda *a, **k: calls.append(a) or True)
    chat = "972500000001@s.whatsapp.net"
    db.execute("INSERT INTO messages VALUES ('O1', ?, 'x', 'פגישה עם הספק', ?, 0, '', '')", (chat, dt.datetime.now().isoformat()))
    db.commit()
    _own(db, "O2", chat, "", 1, media="image")
    _own(db, "O3", chat, mod.CFG["reply_prefix"] + "פגישה עם הספק", 1)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    for m in ("O1", "O2", "O3", "missing"):
        mod.fire_icons(chat, m)
    # owner 19.11 "האש לא עובדת": nothing is edited and nothing goes to the other person, but a 🔥 that changes nothing
    # is explained in the control group (others' message, media, missing); the bot's own answer stays silent
    assert not [c for c in calls if c and c[0] == "/edit_message"]
    assert all(c[0] == mod.PRIMARY for c in calls)
    assert len(calls) == 3


def test_fire_reaction_is_queued_once(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "watches_people", lambda: True)
    monkeypatch.setattr(mod, "note_reaction", lambda *a: None)
    started = []
    monkeypatch.setattr(mod, "fire_icons", lambda c, m: started.append((c, m)))
    ev = {"type": "reaction", "chat_jid": "972500000001@s.whatsapp.net", "message_id": "R1", "emoji": "🔥"}
    mod.handle_reaction_event(ev)
    mod.handle_reaction_event(json.loads(json.dumps(ev)))
    time.sleep(0.3)
    assert started == [("972500000001@s.whatsapp.net", "R1")]  # own thread, not behind a long run in the work queue (owner 19.11)


def test_meter_full_marks_private_claude_unavailable(agent, monkeypatch):
    mod, _ = agent
    t = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=10)).isoformat()
    # owner 22.11: meter_full reads only what entering the provider read once (no meter read of its own)
    monkeypatch.setitem(mod._usage_cache, "at", mod.time.time())
    monkeypatch.setitem(mod._usage_cache, "v", {"seven_day": {"utilization": 100.0, "resets_at": t}})
    monkeypatch.setattr(mod, "claude_usage", lambda max_age=0: (_ for _ in ()).throw(AssertionError("no meter read of its own")))
    monkeypatch.setattr(mod, "limit_raw", lambda: None)
    assert mod.meter_full()[0] == "seven_day"
    assert not mod.provider_ready("claude")
    st = mod.provider_status("claude")
    assert not st["ok"] and "100%" in st["detail"]


def test_copilot_login_is_named_and_cools(agent):
    mod, _ = agent
    raw = "Error: No authentication information found. Run the '/login' command • Set the COPILOT_GITHUB_TOKEN, GH_TOKEN"
    assert mod.classify_down(raw)[0] == "login"
    line = mod.error_line_he(raw, "copilot")
    assert "לא מחוברת" in line and "Claude" not in line


def test_a_silent_meter_never_blocks_entering_claude(agent, monkeypatch):
    """Owner 22.11: /ספק 1 failed with "המנוי לא מחובר: לא הצלחתי לקרוא את מד השימוש" while the meter only answered 429."""
    mod, _ = agent
    reads = []
    monkeypatch.setattr(mod, "claude_usage", lambda max_age=120: reads.append(max_age))
    c = mod.provider_check("claude")
    assert c["ok"] and "לא חוסם" in c["detail"] and reads == [0], "read once, on entering, and never blocking"
