"""Owner 21.11 20:00: the bridge's browser and web search for every AI (whatsapp-mcp-server/browser.py). No browser, no network."""
import base64
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("bridge_browser", os.path.join(ROOT, "whatsapp-mcp-server", "browser.py"))
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)


def test_web_search_goes_down_the_three_tools_and_never_gets_stuck(monkeypatch):
    calls = []

    def fail(name):
        return lambda q, n: calls.append(name) or (_ for _ in ()).throw(RuntimeError(f"{name} down"))
    monkeypatch.setattr(br, "ENGINES", (("LangSearch", fail("a")), ("Tavily", lambda q, n: calls.append("b") or []),
                                        ("Jina", lambda q, n: calls.append("c") or [{"title": "t", "url": "u", "snippet": "s"}])))
    r = br.web_search("שאלה", 3)
    assert r["engine"] == "Jina" and calls == ["a", "b", "c"]
    monkeypatch.setattr(br, "ENGINES", (("LangSearch", fail("a")),))
    r = br.web_search("שאלה")
    assert r["engine"] is None and r["results"] == [] and "LangSearch: a down" in r["tried"][0]


def test_a_missing_key_is_said_plainly(monkeypatch, tmp_path):
    monkeypatch.setattr(br, "ROOT", str(tmp_path))
    for fn, name in ((br.search_langsearch, "langsearch"), (br.search_tavily, "tavily"), (br.search_jina, "jina")):
        try:
            fn("q", 3)
            assert False
        except RuntimeError as e:
            assert f"secrets/{name}.key" in str(e)


def test_bings_redirect_gives_the_real_address():
    real = "https://www.example.com/page?x=1"
    href = "https://www.bing.com/ck/a?!&&p=abc&u=a1" + base64.urlsafe_b64encode(real.encode()).decode().rstrip("=") + "&ntb=1"
    assert br.bing_target(href) == real and br.bing_target("https://plain.example/") == "https://plain.example/"
