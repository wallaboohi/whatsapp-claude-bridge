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
    monkeypatch.setattr(mod, "_probe_http", lambda p: (True, ""))
    assert mod.provider_probe("groq")["answered"] is True
    monkeypatch.setattr(mod, "_probe_http", lambda p: (False, "HTTP 429 rate limit exceeded"))
    r = mod.provider_probe("groq")
    assert r["answered"] is False and "קוד 429 (" in r["he"]


def test_probe_report_lists_every_provider_with_availability_and_answer(agent, monkeypatch):
    mod, _ = agent
    fake = lambda n: {"name": n, "available": n != "ollama", "answered": {"groq": True, "codex": False}.get(n), "seconds": 0.4,  # noqa: E731
                      "he": "קוד 429 (x): y" if n == "codex" else "", "reason": "", "raw": ""}
    monkeypatch.setattr(mod, "provider_probe", fake)
    out = mod.owner_command("/בדוק מענה", mod.PRIMARY)
    assert "זמינות ומענה" in out and "✅ ענה (0.4 שניות)" in out and "⛔ לא ענה" in out and "קוד 429" in out and "⚪ לא זמין" in out
