"""Targeted tests for 19.11 fixes: provider numbering/auto exclusion, /מודל on non-Claude providers, and StatusMsg hardening."""
import datetime as dt

from test_agent import agent  # noqa: F401


def _make_agent(agent, monkeypatch):
    mod, _ = agent
    # keep tests deterministic and offline
    monkeypatch.setattr(mod, "provider_probe", lambda *a, **k: {"answered": True, "seconds": 0.1, "he": ""})
    monkeypatch.setattr(mod, "provider_check", lambda name, model=None: {"ok": True, "detail": "ok", "models": list((mod.providers().get(name) or {}).get("models") or []), "tried": []})
    monkeypatch.setattr(mod, "journal_real_recent", lambda *a, **k: (0, 0))
    monkeypatch.setattr(mod, "note_owner_correction", lambda *a, **k: None)
    monkeypatch.setattr(mod, "rename_for_command", lambda *a, **k: None)
    monkeypatch.setattr(mod, "sync_group_name", lambda *a, **k: None)
    monkeypatch.setattr(mod, "ai_status_lines", lambda *a, **k: [])
    monkeypatch.setattr(mod, "routing_note", lambda *a, **k: "")
    return mod


def test_provider_order_excludes_auto(agent, monkeypatch):
    mod = _make_agent(agent, monkeypatch)
    order = mod.provider_order()
    assert "auto" not in order
    assert all(n != "auto" for n in order)


def test_provider_number_zero_for_auto(agent, monkeypatch):
    mod = _make_agent(agent, monkeypatch)
    assert mod.provider_number("auto") == 0
    assert mod.provider_switch_words("auto") == []


def test_provider_model_pick_by_number_and_name(agent, monkeypatch):
    mod = _make_agent(agent, monkeypatch)
    # groq has known models in providers.json; pick from its list
    ms = mod.provider_model_list("groq")
    assert ms, "test expects groq to have models"
    picked, err = mod._provider_model_pick("groq", "1")
    assert err is None and picked == str(ms[0])
    short = str(ms[0]).split("/")[-1].split(":")[0]
    picked2, err2 = mod._provider_model_pick("groq", short)
    assert err2 is None and picked2 == str(ms[0])
    _, err3 = mod._provider_model_pick("groq", "999")
    assert err3 and "1" in err3
    _, err4 = mod._provider_model_pick("groq", "no_such_model_xyz")
    assert err4 and "לא מצאתי" in err4


def test_slash_model_resolves_non_claude_provider_model(agent, monkeypatch):
    mod = _make_agent(agent, monkeypatch)
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("groq", None))
    monkeypatch.setattr(mod, "cur_chat", lambda: "g@g.us")
    calls = []
    monkeypatch.setattr(mod, "switch_provider", lambda name, model=None, chat=None: calls.append((name, model, chat)) or "ok")
    ms = mod.provider_model_list("groq")
    short = str(ms[0]).split("/")[-1].split(":")[0]
    res = mod.owner_command("/מודל " + short, chat="g@g.us")
    assert res == "ok"
    assert calls and calls[-1][0] == "groq" and calls[-1][1] == str(ms[0])
    calls.clear()
    mod.owner_command("/מודל 1", chat="g@g.us")
    assert calls and calls[-1][1] == str(ms[0])


def test_status_edit_landed_normalizes_and_rejects_preparing(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "status_ai_info", lambda *a, **k: ("x", "", ""))
    st = mod.StatusMsg("sonnet", "g@g.us", wa=False)
    st.mid = "m1"

    # "בהכנה" still present -> not landed
    class _DB:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, q, p):
            class R:
                def fetchone(self_inner):
                    return ("📝 תשובה: ⏳ בהכנה\nhello",)
            return R()
    monkeypatch.setattr(mod, "mdb", lambda: _DB())
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    assert st._edit_landed("hello", wait=0.1) is False

    # normalized probe matches (RTL marks / decoration stripped) and no בהכנה -> landed
    class _DB2:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, q, p):
            class R:
                def fetchone(self_inner):
                    return ("\u200f_שלום_ עולם",)
            return R()
    monkeypatch.setattr(mod, "mdb", lambda: _DB2())
    assert st._edit_landed("שלום עולם", wait=0.1) is True


def test_status_finalize_waits_longer_for_slow_edit_interval(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "status_ai_info", lambda *a, **k: ("x", "", ""))
    monkeypatch.setitem(mod.CFG, "status_edit_seconds", 20)
    st = mod.StatusMsg("sonnet", "g@g.us", wa=False)
    st.mid = "m1"
    st.wa = True
    waited = []
    st.loop_done.wait = lambda t: waited.append(t) or True
    # make edit path exit early (no answer merge attempted in a way that matters) by giving no answer? 
    # finalize: waits, then if not mid/wa return False; mid/wa true and answer None -> tries final edit only
    monkeypatch.setattr(mod, "bridge", lambda *a, **k: {"success": True})
    st.finalize(None)
    assert waited and waited[0] >= 75  # max(30, 3*20+15)
