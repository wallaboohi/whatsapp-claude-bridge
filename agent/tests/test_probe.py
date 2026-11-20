"""Owner 16.11: availability + real-answer check of every AI, and every provider's fault number passed on and translated to Hebrew."""
import re

from test_agent import agent  # noqa: F401

# real fault texts seen from each provider on 16.11 (shortened)
FAULTS = {
    "codex": ("ERROR: You've hit your usage limit. Upgrade to Plus to continue using Codex, or try again at Dec 14th, 2026 1:11 PM.", "quota", None),
    "copilot": ("You have exceeded your monthly quota (Request ID: 00000-2838b279-8181-4a1f-95d5-1)", "quota", None),
    "gemini_cli": ('{"error": {"message": "You have exhausted your daily quota on this model.", "code": 429}}', "quota", 429),
    "gemini": ("API Error: 429 You exceeded your current quota, please check your plan and billing details", "rate", 429),
    "groq": ("[API Error: 413 Request too large for model on tokens per minute (TPM): Limit 8000, Requested 14123]", "rate", 413),
    "openrouter_free": ("HTTP 401 Unauthorized", "key", 401),
    "nvidia": ("HTTP 503 service unavailable", "rate", 503),
    "ollama": ("connection failed: URLError: <urlopen error [WinError 10061] connection refused>", "unreachable", None),
}


def test_every_provider_fault_is_classified_and_translated(agent):
    mod, _ = agent
    for name, (raw, reason, code) in FAULTS.items():
        assert mod.classify_down(raw)[0] == reason, name
        line = mod.error_line_he(raw, name)
        assert re.search(r"[א-ת]{3}", line) and "הגשר:" in line, name
        if code:
            assert f"קוד {code} (" in line and mod.CODE_HE[code] in line, name
        else:
            assert "בלי קוד שגיאה" in line, name
        assert raw[:25] not in line, name  # the raw English text is never the message


def test_probe_reports_a_cli_that_ran_out_of_quota(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "provider_status", lambda n: {"ok": True, "detail": "מותקן"})

    def down(name, p, *a, **k):
        raise mod.ProviderDown(name, "quota", 60, raw=FAULTS["codex"][0])
    monkeypatch.setattr(mod, "_run_cli", down)
    r = mod.provider_probe("codex")
    assert r["available"] and r["answered"] is False and r["reason"] == "quota" and "מכסת השימוש" in r["he"]
    assert getattr(mod._ctx, "probe", False) is False  # the probe flag never leaks into a real conversation


def test_probe_reports_an_api_provider_answer_and_fault(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "provider_status", lambda n: {"ok": True, "detail": "זמין"})
    monkeypatch.setattr(mod, "_probe_http", lambda p, big=False: (True, ""))
    assert mod.provider_probe("groq")["answered"] is True
    monkeypatch.setattr(mod, "_probe_http", lambda p, big=False: (False, "HTTP 429 rate limit exceeded"))
    r = mod.provider_probe("groq")
    assert r["answered"] is False and "קוד 429 (" in r["he"]


def test_probe_report_lists_every_provider_with_availability_and_answer(agent, monkeypatch):
    mod, _ = agent
    fake = lambda n: {"name": n, "available": n != "ollama", "answered": {"groq": True, "codex": False}.get(n), "seconds": 0.4,  # noqa: E731
                      "he": "קוד 429 (x): y" if n == "codex" else "", "reason": "", "raw": ""}
    monkeypatch.setattr(mod, "provider_probe", fake)
    out = mod.owner_command("/בדוק מענה", mod.PRIMARY)
    assert "זמינות ומענה" in out and "✅ ענה (0.4 שניות)" in out and "⛔ לא ענה" in out and "קוד 429" in out and "⚪ לא זמין" in out


def test_numbers_and_spellings_switch_the_provider(agent):
    mod, _ = agent
    r = mod.resolve_ai_word
    assert r("3")[0] == "gemini" and r("11")[0] == "codex" and r("1")[0] == "claude" and r("99") is None
    for w in ("Gemini-CLI", "gemini cli", "geminicli", "גמיני סיאלאיי".replace(" ", ""), "GEMINI_CLI"):
        assert r(w)[0] == "gemini_cli", w
    for w in ("ג'מיני", "גמיני", "gamini", "Gemini"):
        assert r(w)[0] == "gemini", w
    assert r("קופיילוט")[0] == r("קופילוט")[0] == r("copilot")[0] == "copilot"
    assert r("open-router")[0] == "openrouter_free" and r("openrauter")[0] == "openrouter_free"


def test_explicit_provider_number_command(agent, monkeypatch):
    mod, _ = agent
    calls = []
    monkeypatch.setattr(mod, "switch_provider", lambda name, model=None, chat=None: calls.append((name, model, chat)) or "ok")
    assert mod.owner_command("/ספק 3", mod.PRIMARY) == "ok"
    assert calls[-1] == ("gemini", None, mod.PRIMARY)
    assert "אין ספק מספר 99" in mod.owner_command("/ספק 99", mod.PRIMARY)


def test_the_owners_typos_are_understood_and_other_words_are_not(agent):
    mod, _ = agent
    assert mod.fuzzy_ai_word("claudeflare") == "cloudflare" or mod.resolve_ai_word("claudeflare")[0] == "cloudflare"
    assert mod.fuzzy_ai_word("cloudflarr") == "cloudflare" and mod.fuzzy_ai_word("geminii") == "gemini"
    for w in ("אופוס", "חייקו", "סונט", "מודל", "עצור", "כרום", "opus", "haiku", "sonnet", "מצב", "בדוק", "plugin", "1"):
        assert mod.fuzzy_ai_word(w) is None, w  # commands and model words are never read as a provider


def test_switch_by_number_and_by_misspelled_name(agent, monkeypatch):
    mod, _ = agent
    got = []
    monkeypatch.setattr(mod, "switch_provider", lambda name, model=None, chat=None: got.append(name) or "ok")
    assert mod.owner_command("/3", mod.PRIMARY) == "ok" and got[-1] == "gemini"
    assert mod.owner_command("/gemini cli", mod.PRIMARY) == "ok" and got[-1] == "gemini_cli"
    out = mod.owner_command("/cloudflarr", mod.PRIMARY)
    assert out.endswith("ok") and "הבנתי /cloudflarr" in out and got[-1] == "cloudflare"
    assert mod.owner_command("/אולמה", mod.PRIMARY) == "ok" and got[-1] == "ollama"


def test_opencode_check_does_not_ask_for_a_key(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "opencode_bin", lambda: "C:/x/opencode-cli.exe")
    c = mod.provider_check("opencode")
    assert c["ok"] and "מפתח" not in c["detail"]


def test_switch_message_gives_reason_code_and_groups_every_ai(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "provider_ready", lambda n: n not in ("opencode", "nvidia"))
    mod.ai_state_set("gemini", True)
    mod.ai_state_set("codex", False, "quota", FAULTS["codex"][0])
    mod.ai_state_set("cloudflare", False, "rate", "HTTP 429 rate limit")
    mod.ai_state_set("nvidia", False, "key", "missing API key")
    out = mod.ai_down_text("copilot", "x", FAULTS["copilot"][0])
    assert "*Copilot* לא זמין מהסיבה: מכסת השימוש נגמרה" in out and "לא עברתי לבינה אחרת" in out
    assert "✅ *זמינים וענו לאחרונה:*" in out and "*/ספק 3*" in out and "Gemini" in out
    assert "⏳ *ממתינים למכסה או לאיפוס:*" in out and "*/ספק 11*" in out and "Codex" in out and "14.12" in out
    assert "*/ספק 7*" in out and "Cloudflare" in out and "קוד 429" in out
    assert "⚙️ *דורשים הגדרה או התחברות:*" in out and "*/ספק 6*" in out and "NVIDIA" in out
    assert "⚪ *לא זמינים במחשב הזה:*" in out and "*/ספק 8*" in out and "OpenCode" in out
    assert "❔ *מוכנים, עוד לא נבדקו:*" in out
    assert "למעבר בין ספקים שלח /ספק *מספר*" in out and "/copilot" not in out and "*/ספק 10*" not in out
    assert "14.12" in mod.retry_hint_he(FAULTS["codex"][0]) and "16 דקות" in mod.retry_hint_he("Please retry in 16m7.9s.")


def test_provider_status_rows_keep_icon_column_and_location(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "provider_ready", lambda _name: True)
    monkeypatch.setattr(mod, "where_of", lambda name, model=None: ("local", "מקומי") if mod.provider_is_local(name) else ("remote", "ענן"))
    mod.ai_state_set("claude_work", True)
    rows = [line for line in mod.ai_status_lines() if "*/ספק " in line]
    assert rows and all(line.startswith(mod.RLM + "*/ספק ") for line in rows)
    cloud = [line for line in rows if "☁️" in line]
    assert len({line.index("☁️") for line in cloud}) == 1
    work = next(line for line in cloud if "claude_work" not in line and "Claude" in line and "עבודה" in line)
    assert "*/ספק 12*" in work and "מחשב עבודה נייד" in work


def test_model_menu_on_another_ai_shows_its_own_models_not_claudes(agent, monkeypatch):
    mod, _ = agent
    monkeypatch.setattr(mod, "active_provider", lambda chat=None: ("groq", "openai/gpt-oss-120b"))
    out = mod.models_guide(mod.PRIMARY)
    assert "גרוק" in out and "*/מודל 1*" in out and "Haiku" not in out and "הייקו" not in out  # owner 18.11 22:58: its own models, numbered
