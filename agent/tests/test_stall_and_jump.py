"""Owner 20.11: a run with no movement for 5 minutes is stopped and resumed when its provider answers again; /עכשיו and >>
jump the queue (the running work stops, the message is answered first, then the work goes on)."""
import threading
import time

from test_agent import agent  # noqa: F401


def _cmd(mod, chat, text, status="pending", prio=0, attempts=0):
    with mod.adb() as db:
        return db.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync, status, prio, attempts) "
                          "VALUES (?, ?, '2026-11-20 00:10', ?, 'wa', 0, ?, ?, ?)",
                          (f"m{time.time_ns()}", text, chat, status, prio, attempts)).lastrowid


def test_the_queue_takes_the_urgent_message_first(agent):
    mod, _ = agent
    chat = "1203630101@g.us"
    _cmd(mod, chat, "old one")
    _cmd(mod, chat, "urgent", prio=2)
    _cmd(mod, chat, "resume", prio=1)
    assert [mod.claim_next(chat)[2] for _ in range(3)] == ["urgent", "resume", "old one"]


def test_jump_queue_stops_the_running_work_and_resumes_it_after(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630102@g.us"
    _cmd(mod, chat, "בנה את המודול הגדול", status="running")
    sent, stopped = [], []
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: sent.append(text))
    monkeypatch.setattr(mod, "stop_chat", lambda c: stopped.append(c) or 1)
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("codex_work", None))
    with mod._workers_lock:
        mod._workers.add(chat)
    try:
        assert mod.jump_queue(chat, "J1", ">> מה השעה?")
    finally:
        with mod._workers_lock:
            mod._workers.discard(chat)
        try:
            mod.os.remove(mod.STOP_FILE)
        except OSError:
            pass
    assert stopped == [chat] and "⏭" in sent[-1]
    first, second = mod.claim_next(chat), mod.claim_next(chat)
    assert first[2] == "מה השעה?" and "המשך את העבודה" in second[2] and "בנה את המודול הגדול" in second[2]


def test_jump_queue_with_nothing_running_just_goes_first(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630103@g.us"
    _cmd(mod, chat, "waiting one")
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: None)
    monkeypatch.setattr(mod, "stop_chat", lambda c: (_ for _ in ()).throw(AssertionError("nothing to stop")))
    assert mod.jump_queue(chat, "J2", "/עכשיו תענה קודם על זה")
    assert mod.claim_next(chat)[2] == "תענה קודם על זה"
    assert not mod.jump_queue(chat, "J3", "הודעה רגילה")


def test_a_run_without_movement_is_stopped(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630104@g.us"

    class P:
        def poll(self):
            return None
    monkeypatch.setitem(mod.CFG, "run_stall_minutes", 0.001)
    stopped = []
    monkeypatch.setattr(mod, "stop_chat", lambda c: stopped.append(c) or 1)
    with mod._run_procs_lock:
        mod._run_procs[chat] = {P()}
    mod._progress_at[chat] = time.time() - 60
    done = threading.Event()
    monkeypatch.setattr(done, "wait", lambda t: False)
    try:
        mod.stall_watch(chat, done)
    finally:
        with mod._run_procs_lock:
            mod._run_procs.pop(chat, None)
    assert stopped == [chat] and chat in mod._stalled
    mod._stalled.discard(chat)


def test_a_stalled_run_waits_for_its_provider_and_comes_back(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630105@g.us"
    cid = _cmd(mod, chat, "עבודה ארוכה", status="running", attempts=1)
    sent = []
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: sent.append(text))
    monkeypatch.setattr(mod, "react", lambda *a, **k: None)
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("codex_work", None))
    monkeypatch.setattr(mod, "_blocked_until", lambda name: "")
    mod._ctx.provider_used = "codex_work"
    assert mod.park_stalled(cid, chat, "M", "wa") is True
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE id = ?", (cid,)).fetchone()[0] == "stalled"
    assert "לא זז" in sent[-1] and mod.kv_get(f"cut:{cid}")
    monkeypatch.setattr(mod, "provider_probe", lambda name: {"answered": False})
    mod._stall_check(cid, chat, "M", "wa")
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE id = ?", (cid,)).fetchone()[0] == "stalled"
    monkeypatch.setattr(mod, "provider_probe", lambda name: {"answered": True})
    mod._stall_check(cid, chat, "M", "wa")
    with mod.adb() as db:
        assert db.execute("SELECT status FROM commands WHERE id = ?", (cid,)).fetchone()[0] == "pending"
    assert "חזר לענות" in sent[-1]
    mod._ctx.provider_used = None


def test_after_three_stalls_the_owner_decides(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630106@g.us"
    cid = _cmd(mod, chat, "x", status="running", attempts=3)
    sent = []
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: sent.append(text))
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("opencode", None))
    assert mod.park_stalled(cid, chat, "M", "wa") is False and "לא אמשיך לבד" in sent[-1]


def test_the_wheel_sent_as_a_reply_reads_the_replied_message(agent, monkeypatch):
    """Owner 20.11 12:17: 🛞 sent as a reply to a message reads THAT message aloud, not the last answer."""
    mod, _ = agent
    monkeypatch.setattr(mod, "mdb", lambda: _FakeMdb({"W1": "Q9"}))
    assert mod.quoted_id_of("g@g.us", "W1") == "Q9"
    assert mod.quoted_id_of("g@g.us", "W2", wait=0) == ""


class _FakeMdb:
    def __init__(self, quotes):
        self.quotes = quotes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, params):
        mid = params[0]
        row = (self.quotes[mid],) if mid in self.quotes else None

        class R:
            def fetchone(self_inner):
                return row
        return R()
