"""Owner 14.11: the free-model menu, the approval before a slow run, and the side question."""
import json

from test_agent import agent  # noqa: F401  (the fixture: a fresh agent module on a temporary database)


class _FakeResp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return json.dumps(self._b).encode()


def test_free_menu_lists_local_and_remote_and_picks_by_number(agent, monkeypatch):
    mod, _ = agent
    hosts = [{"url": "http://127.0.0.1:11435", "label": "לפטופ (ברירת מחדל)"}, {"url": "http://127.0.0.1:11434", "label": "נייח (גיבוי)"}]
    tags = {"11435": [{"name": "bridge-qwen3-4b:latest", "size": 2.5e9}, {"name": "qwen3:8b", "size": 5e9}], "11434": [{"name": "bridge-qwen3-8b:latest", "size": 5.2e9}]}
    monkeypatch.setattr(mod, "provider_hosts", lambda p: hosts)
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda url, timeout=0: _FakeResp({"models": tags["11435" if "11435" in url else "11434"]}))
    monkeypatch.setattr(mod, "provider_ready", lambda n: n in ("groq", "claude"))
    monkeypatch.setattr(mod, "openrouter_free_models", lambda: ["a/free-1:free", "b/free-2:free"])
    text = mod.free_menu_text(mod.PRIMARY)
    assert "1. ✅ 🏠 לפטופ · bridge-qwen3-4b" in text and "🏠 נייח · bridge-qwen3-8b" in text  # local first, with the computer it runs on
    assert "qwen3:8b" not in text.replace("bridge-qwen3-8b", "")  # only the tuned bridge- variants are offered locally
    assert "☁️ Groq · gpt-oss-120b" in text and "⚪ ☁️ Gemini" in text and "Claude (מנוי)" in text
    picked = []
    monkeypatch.setattr(mod, "switch_provider", lambda n, m=None: picked.append((n, m)) or "✅")
    assert mod.owner_command("/מודל חינמי 1", mod.PRIMARY) == "✅" and picked == [("ollama", "bridge-qwen3-4b:latest")]
    gemini_no = next(int(ln.split(".")[0]) for ln in text.splitlines() if "Gemini" in ln)  # the number printed in front of the line
    assert "לא זמינה" in mod.owner_command(f"/מודל חינמי {gemini_no}", mod.PRIMARY)
    assert "אין מספר" in mod.owner_command("/מודל חינמי 99", mod.PRIMARY)


def test_a_run_that_crosses_five_minutes_waits_for_the_owners_ok(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('m9', 'בקשה כבדה', '2026-11-14 02:00:00', ?, 'wa', 0)", (mod.PRIMARY,))
        cid = db.execute("SELECT id FROM commands WHERE msg_id = 'm9'").fetchone()[0]
    assert mod.owner_command("כן", mod.PRIMARY) is None  # nothing waits: "כן" is an ordinary message
    mod._ctx.chat = mod.PRIMARY
    try:
        mod.ask_slow_approval(cid, mod.PRIMARY, "m9", "wa", (600, "ollama", "bridge-qwen3-4b:latest", "לפטופ"))
    finally:
        mod._ctx.chat = None
    assert "10 דקות" in sent[-1] and "מקומי, לפטופ" in sent[-1] and "כן" in sent[-1]
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE id = ?", (cid,)).fetchone()[0] == "waiting_approval"
    assert "מאשר" in mod.owner_command("כן", mod.PRIMARY)
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE id = ?", (cid,)).fetchone()[0] == "pending"
    assert mod.kv_get(f"approved:{cid}") == "1" and mod.owner_command("כן", mod.PRIMARY) is None  # one approval, one run
    # the estimate: Claude is quick, a local 4B model on the laptop is not
    monkeypatch.setattr(mod, "active_provider", lambda *a: ("claude", None))
    assert mod.estimate_run_seconds("שלום", "sonnet")[0] < mod.slow_confirm_seconds()
    monkeypatch.setattr(mod, "active_provider", lambda *a: ("ollama", "bridge-qwen3-4b:latest"))
    monkeypatch.setattr(mod, "provider_hosts", lambda p: [{"url": "http://127.0.0.1:1", "label": "לפטופ"}])
    assert mod.estimate_run_seconds("שלום", "sonnet")[0] >= mod.slow_confirm_seconds()


def test_cancelling_a_slow_run_closes_it(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: True)
    with mod.adb() as db:
        db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES ('m8', 'x', '2026-11-14 02:00:00', ?, 'wa', 0)", (mod.PRIMARY,))
        cid = db.execute("SELECT id FROM commands WHERE msg_id = 'm8'").fetchone()[0]
    mod._ctx.chat = mod.PRIMARY
    try:
        mod.ask_slow_approval(cid, mod.PRIMARY, "m8", "wa", (900, "ollama", "m", "נייח"))
    finally:
        mod._ctx.chat = None
    assert "בוטל" in mod.owner_command("בטל", mod.PRIMARY)
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE id = ?", (cid,)).fetchone()[0] == "done"


def test_side_question_is_answered_at_once_and_never_queued(agent, monkeypatch):
    mod, _ = agent
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, media=None, quoted=None: sent.append(text) or True)
    monkeypatch.setattr(mod, "react", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(mod, "_run_auto", lambda prompt, model, sid, *a, **k: seen.update(prompt=prompt, fork=k.get("fork"), tools=k.get("tools"), stoppable=k.get("stoppable"))
                        or {"result": "המצב: הבדיקות רצות, עוד כדקה."})
    mod.kv_set(mod.K("session_id", mod.PRIMARY), "sess-1")
    mod.enqueue_command("side1", "/תוך כדי מה המצב?", "2026-11-14 02:00:00", mod.PRIMARY)
    deadline = mod.time.time() + 5
    while not sent and mod.time.time() < deadline:
        mod.time.sleep(0.05)
    assert sent and "תוך כדי" in sent[-1] and "הבדיקות רצות" in sent[-1]
    assert seen["fork"] is True and seen["tools"] == "Read,Glob,Grep" and seen["stoppable"] is False and "מה המצב?" in seen["prompt"]
    with mod.adb() as db:
        assert db.execute("SELECT count(*) FROM commands WHERE msg_id = 'side1'").fetchone()[0] == 0  # not behind the running work
    assert mod.SIDE_RE.match("/btw hello") and mod.SIDE_RE.match("/ תוך כדי x") and not mod.SIDE_RE.match("/תוך כדיx")
