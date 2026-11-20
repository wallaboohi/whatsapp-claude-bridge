"""Owner 19.11 19:30: one "מדבר" line, /עצור for any provider, a silent run never holds the queue (30 s), orphan
processes are cleared, usage meters under every provider, the 90% rule, the availability check at 06:00 and 18:00."""
import json
import sys
import threading
import time

from test_agent import agent  # noqa: F401


def test_speaker_line_is_one_line_with_all_four(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setitem(mod.CFG, "host_label", "YB-PC-HW")
    line = mod.speaker_line("קלוד עבודה", "opus", "☁️ ענן")
    assert line.startswith("🗣️ *מדבר:* קלוד עבודה")
    assert "☁️ בענן" in line and "במחשב יקיר נייח" in line and "במודל " + mod.model_he("opus") in line
    assert "\n" not in line
    assert "🏠 לפטופ" in mod.speaker_line("אולמה", None, "🏠 לפטופ")
    st = mod.StatusMsg("sonnet", wa=False)
    text = st._text()
    st.finish()
    route = [l for l in text.split(chr(10)) if "נתיב" in l]  # owner 20.11: the route row in the status message
    assert len(route) == 1 and "🖥️" in route[0] and "יקיר" in route[0]


def test_stop_kills_a_tracked_process_and_the_run_is_stopped(agent):
    mod, _ = agent
    chat = "1203630001@g.us"
    box = {}

    def run():
        try:
            mod._run_tracked([sys.executable, "-c", "import time; time.sleep(60)"], chat, text=True)
            mod._raise_if_stopped(chat)
            box["r"] = "finished"
        except mod.Stopped:
            box["r"] = "stopped"
    t = threading.Thread(target=run)
    t.start()
    for _ in range(50):
        with mod._run_procs_lock:
            if mod._run_procs.get(chat):
                break
        time.sleep(0.1)
    assert mod.stop_chat(chat) == 1
    t.join(30)
    assert box.get("r") == "stopped"
    with mod._run_procs_lock:
        assert not mod._run_procs.get(chat)
        mod._stop_wanted.discard(chat)


def test_a_closed_run_is_reported_as_stopped_not_as_a_provider_fault(agent, monkeypatch):
    mod, _ = agent
    def boom(*a, **k):
        raise RuntimeError("closed: the run's process ended with no answer and no error (stopped from outside or crashed)")
    monkeypatch.setattr(mod, "_run_claude", boom)
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("opencode", None))
    out = mod.claude("x", on_stage=lambda *a: None, chat=mod.PRIMARY)
    assert out.startswith("⛔ העבודה נעצרה באמצע") and "לא זמין" not in out


def test_live_stop_reaches_a_non_claude_run(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630002@g.us"
    sent, stopped = [], []
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: sent.append(text))
    monkeypatch.setattr(mod, "stop_chat", lambda c: stopped.append(c) or 1)
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("opencode", None))
    with mod._workers_lock:
        mod._workers.add(chat)  # an OpenCode run: a worker, but no Claude _active_runs entry
    try:
        assert mod.live_control(chat, "S1", "/עצור")
    finally:
        with mod._workers_lock:
            mod._workers.discard(chat)
        try:
            mod.os.remove(mod.STOP_FILE)
        except OSError:
            pass
    assert stopped == [chat] and "⛔" in sent[-1]


def test_a_silent_run_releases_the_next_message_beside_it(agent, monkeypatch):
    mod, _ = agent
    chat = "1203630003@g.us"
    ran, notes = [], []
    monkeypatch.setattr(mod, "claim_next", lambda c: ("id1", "M1", "שאלה", "2026-11-19 19:40", c, "wa", 0))
    monkeypatch.setattr(mod, "run_command", lambda row: ran.append((row[0], getattr(mod._ctx, "side_run", False))))
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: notes.append(text))
    mod._progress_at[chat] = time.time() - 45
    with mod._workers_lock:
        mod._side_workers.add(chat)
    mod.side_worker(chat)
    assert ran == [("id1", True)], "the queued message ran, as a side run (its own session)"
    assert "במקביל" in notes[-1] and chat not in mod._side_workers
    assert mod.release_seconds() == 30.0


def test_reaper_stops_only_orphans_of_known_programs(agent, monkeypatch):
    mod, _ = agent
    rows = [{"pid": 11, "name": "ssh.exe", "cmd": "ssh 4-MINE codex exec", "age": 4000, "orphan": True},
            {"pid": 12, "name": "ssh.exe", "cmd": "ssh x", "age": 30, "orphan": True},  # too young: maybe starting
            {"pid": 13, "name": "claude.exe", "cmd": "claude.exe", "age": 9999, "orphan": True},  # interactive: never
            {"pid": 14, "name": "claude.exe", "cmd": "claude -p --output-format stream-json", "age": 9999, "orphan": True},
            {"pid": 15, "name": "node.exe", "cmd": "C:\\Program Files\\WindowsApps\\OpenAI.Codex\\node.exe codex", "age": 9999, "orphan": True},
            {"pid": 16, "name": "notepad.exe", "cmd": "notepad", "age": 9999, "orphan": True}]
    killed = []

    class R:
        def __init__(self, out=""):
            self.stdout, self.returncode = out, 0

    def fake_run(cmd, **k):
        if cmd[0] == "powershell":
            return R(json.dumps(rows))
        killed.append(int(cmd[-1]))
        return R()
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.reap_orphans() == 2 and killed == [11, 14]


def test_meters_line_and_the_90_percent_rule(agent, monkeypatch):
    mod, _ = agent
    now = mod.dt.datetime.now().isoformat(timespec="seconds")
    mod.kv_set("meter:codex_work", json.dumps({"at": now, "windows": [{"w": "5 שעות", "pct": 40, "reset": "21:00"},
                                                                       {"w": "שבועי", "pct": 93, "reset": "24.11 05:00"}], "note": ""}))
    line = mod.meter_line("codex_work")
    assert "5 שעות 40%" in line and "🔴 שבועי 93%" in line and "יומי —" in line and "חודשי —" in line
    assert mod.meter_high("codex_work") == ("שבועי", 93)
    sent = []
    monkeypatch.setattr(mod, "reply", lambda text, quoted=None: sent.append(text))
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("codex_work", None))
    note = mod.quota_note_for_prompt("1203630004@g.us")
    assert "93%" in note and "אל תתחיל עבודה גדולה" in note
    assert sent and "93%" in sent[0] and "עבודה גדולה" in sent[0]
    mod.quota_note_for_prompt("1203630004@g.us")
    assert len(sent) == 1, "the owner is warned once per provider / window / day, not on every message"
    mod.kv_set("meter:groq", json.dumps({"at": now, "windows": [{"w": "יומי", "pct": 20, "reset": "חצות"}], "note": ""}))
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("groq", None))
    assert mod.quota_note_for_prompt("1203630005@g.us") == ""


def test_meter_of_a_free_api_is_the_bridge_daily_count(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "providers", lambda: {"groq": {"kind": "anthropic", "daily_limit": 1000}})
    monkeypatch.setattr(mod, "provider_is_local", lambda n: False)
    mod.kv_set(f"quota:{mod.dt.date.today().isoformat()}:groq", 250)
    snap = mod.refresh_meter("groq")
    assert snap["windows"][0]["w"] == "יומי" and snap["windows"][0]["pct"] == 25


def test_availability_check_runs_at_six_and_eighteen_with_meters(agent, monkeypatch):
    mod, _ = agent
    import inspect
    src = inspect.getsource(mod.scheduler)
    assert '"engine_probe_time", "06:00"' in src and '"engine_probe_time_pm", "18:00"' in src
    assert "hourly_engine_status)" not in src, "no hourly check any more"
    done = []
    monkeypatch.setattr(mod, "engine_status_report", lambda live=False: done.append("inv"))
    monkeypatch.setattr(mod, "daily_model_check", lambda *a, **k: done.append("models"))
    monkeypatch.setattr(mod, "refresh_all_meters", lambda: done.append("meters"))
    mod.daily_engine_probe()
    assert done == ["inv", "models", "meters"]


def test_a_provider_the_bridge_saw_run_out_is_blocked_in_the_meter(agent, monkeypatch):
    mod, _ = agent
    now = mod.dt.datetime.now()
    mod.kv_set("meter:codex_work", json.dumps({"at": now.isoformat(timespec="seconds"),
                                               "windows": [{"w": "חודשי", "pct": 60, "reset": "x"}], "note": ""}))
    mod.kv_set("limit_until:codex_work", (now + mod.dt.timedelta(hours=9)).isoformat(timespec="seconds"))
    assert "🔒 חסום עד" in mod.meter_line("codex_work") and "חודשי 60%" in mod.meter_line("codex_work")
    assert mod.meter_high("codex_work") == ("חסום", 100)
    mod.kv_set("limit_until:codex_work", "")
    assert mod.meter_high("codex_work") is None


def test_a_hand_back_restart_does_not_wait_forever(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "failover_cfg", lambda: {"for": "נייח"})
    state = {"state": "releasing"}
    monkeypatch.setattr(mod, "failover_state", lambda name: state)
    mod._handback_wait["since"] = 0.0
    assert mod.handback_overdue() is False, "the first look starts the clock"
    mod._handback_wait["since"] = mod.time.time() - 200
    assert mod.handback_overdue() is True
    state["state"] = "idle"
    assert mod.handback_overdue() is False and mod._handback_wait["since"] == 0.0
    monkeypatch.setattr(mod, "failover_cfg", lambda: None)
    assert mod.handback_overdue() is False


def test_a_stale_asked_hold_is_cleared_without_handing_anything_back(agent, monkeypatch, tmp_path):
    mod, _ = agent
    monkeypatch.setattr(mod, "FAILOVER_DIR", str(tmp_path))
    name = "נייח"
    hold = mod._fo_hold_path(name)
    open(hold, "w", encoding="utf-8").write("{}")
    st = {"state": "asked", "asked_at": "2026-11-19T17:34:06", "last": {"since": "x", "back": "y"}}
    mod.failover_save(name, st)
    handed = []
    monkeypatch.setattr(mod, "failover_handback", lambda *a, **k: handed.append(a))
    assert mod.failover_clear_stale(name, st, {}) is True
    assert not mod.os.path.exists(hold) and mod.failover_state(name)["state"] == "idle" and handed == []
    assert mod.failover_state(name)["last"] == {"since": "x", "back": "y"}
    # really holding (plugins loaded here), or the host is down: never touched
    open(hold, "w", encoding="utf-8").write("{}")
    mod.failover_save(name, {"state": "asked"})
    assert mod.failover_clear_stale(name, {"state": "asked"}, {"off_since": "2026-11-19T20:00:00"}) is False
    mod._failover_loaded.add(name)
    try:
        assert mod.failover_clear_stale(name, {"state": "asked"}, {}) is False
    finally:
        mod._failover_loaded.discard(name)
    assert mod.failover_clear_stale(name, {"state": "active"}, {}) is False and mod.os.path.exists(hold)



def test_status_rows_have_one_label_width_and_one_divider(agent):
    """Owner 20.11 10:30 (example ו): every label the same width, ┃ under ┃, the bold on the changing detail, one divider line."""
    mod, _ = agent
    st = mod.StatusMsg("sonnet", wa=False)
    st.set_stage("📖 קורא קבצים", "agent.py")
    text = st._text()
    st.finish()
    rows = text.split(chr(10))
    labels = [r.split("`")[1] for r in rows if r.count("`") >= 2]
    assert len(labels) == 4 and len({len(x) for x in labels}) == 1
    assert all(r.split("`")[2].startswith(" ┃ ") for r in rows if r.count("`") >= 2)
    assert "*agent.py*" in text and "*פעולה*" not in text
    full = st._full("תשובה קצרה")
    assert full.count(mod.StatusMsg.DIVIDER) == 1 and full.index(mod.StatusMsg.DIVIDER) < full.index("📝 *תשובה:*")
