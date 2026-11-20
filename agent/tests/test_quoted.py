"""Owner 20.11 12:17: a message sent as a reply - the AI sees the message it replies to."""
import sqlite3

from test_agent import agent  # noqa: F401


def test_the_quoted_message_goes_with_the_prompt(agent, monkeypatch):
    mod, _ = agent
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT, is_from_me INTEGER,"
               " media_type TEXT, quoted_id TEXT)")
    c = "g@g.us"
    db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", [
        ("q1", c, "me", "🤖 _דוגמה ו: מצב ┃ חושב_", "2026-11-20 10:24:00+02:00", 1, "", None),
        ("m1", c, "me", "נעצרנו עם זה?", "2026-11-20 12:14:42+02:00", 1, "", "q1"),
        ("m2", c, "me", "הודעה רגילה", "2026-11-20 12:15:00+02:00", 1, "", None),
        ("m3", c, "me", "תגובה לישנה", "2026-11-20 12:16:00+02:00", 1, "", "gone"),
        ("q4", c, "972501234567", "x" * 700, "2026-11-20 09:00:00+02:00", 0, "", None),
        ("m4", c, "me", "?", "2026-11-20 12:17:00+02:00", 1, "", "q4")])
    monkeypatch.setattr(mod, "mdb", lambda: db)
    monkeypatch.setattr(mod, "person_name", lambda jid, *a, **k: "ציון")
    out = mod.quoted_context(c, "m1")
    assert out.startswith("↩️ ההודעה הזו היא תגובה להודעה של הגשר (תשובה קודמת) (10:24)") and "דוגמה ו" in out
    assert mod.quoted_context(c, "m2") == ""
    assert "אינה שמורה" in mod.quoted_context(c, "m3")
    long = mod.quoted_context(c, "m4")
    assert "של ציון (09:00)" in long and long.endswith("…»") and len(long) < 700
