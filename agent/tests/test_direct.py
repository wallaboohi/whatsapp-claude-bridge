"""Owner 21.11 (OWNER-SPEC 14): the bridge talks to a provider by itself — folders and INDEX.md, the bridge's tools, the provider's own
toolbox, the WhatsApp tools over MCP stdio, the request / tool rounds, one retry only when the provider says when. No network."""
import json
import os
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge_direct as bd  # noqa: E402


@pytest.fixture()
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "PROVIDERS_DIR", str(tmp_path / "agent" / "providers"))
    monkeypatch.setattr(bd, "ROOT", str(tmp_path))
    monkeypatch.setattr(bd, "HERE", str(tmp_path / "agent"))
    return tmp_path


class FakeMcp:
    def __init__(self):
        self.calls = []

    def tools(self):
        return [{"name": "get_status", "description": "The bridge status.\nmore", "inputSchema": {"type": "object", "properties": {}}}]

    def call(self, name, args, timeout=120):
        self.calls.append((name, args))
        return json.dumps({"connected": True})


def test_folders_for_every_provider_but_1_and_12_and_the_index(box):
    made = bd.ensure_folders(["claude", "auto", "gemini", "groq", "claude_work"])
    prov = box / "agent" / "providers"
    assert (prov / "gemini" / "tools").is_dir() and (prov / "groq" / "README.md").exists() and (prov / "shared" / "tools").is_dir()
    assert not (prov / "claude").exists() and not (prov / "claude_work").exists() and not (prov / "auto").exists()
    index = (prov / "INDEX.md").read_text(encoding="utf-8")
    assert "agent/home" in index and "| gemini |" in index and len(made) == 3
    (prov / "gemini" / "README.md").write_text("mine", encoding="utf-8")
    bd.ensure_folders(["gemini"])
    assert (prov / "gemini" / "README.md").read_text(encoding="utf-8") == "mine", "never overwrites a provider's files"


def test_a_provider_saves_its_own_tool_and_runs_it_next_time(box):
    bd.ensure_folders(["gemini"])
    code = "import json,sys\nargs=json.load(sys.stdin)\nprint('hello', args.get('who'))\n"
    assert "saved" in bd.save_tool("gemini", "hello", "says hello", code)
    assert bd.call_tool("gemini", "hello", {"who": "yakir"}, FakeMcp()).strip() == "hello yakir"
    toc = bd.contents("gemini", FakeMcp())
    assert "- hello: says hello" in toc and "get_status: The bridge status." in toc and "web_search" in toc
    assert "hello" not in bd.contents("groq", FakeMcp()).split("## הכלים שלך")[1], "another provider's toolbox is its own"
    bd.save_tool("gemini", "common", "for all", code, shared=True)
    assert "common" in bd.contents("groq", FakeMcp())


def test_the_bridge_file_tools(box):
    assert "written" in bd.tool_write_file("a/b.txt", "one\ntwo\n")
    assert "1\tone" in bd.tool_read_file("a/b.txt")
    assert "edited" in bd.tool_edit_file("a/b.txt", "two", "three")
    assert "three" in (box / "a" / "b.txt").read_text(encoding="utf-8")
    assert "a/b.txt" in bd.tool_list_files("*.txt")
    assert "b.txt:2: three" in bd.tool_search_text("thr").replace("\\", "/")


def test_web_search_results_are_read_from_the_page():
    page = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fw&amp;rut=1">Weather <b>TLV</b></a>'
            '<a class="result__snippet" href="x">Sunny, 24°</a>'
            '<a class="result__a" href="https://b.example/">Second</a>')
    rows = bd.parse_ddg(page)
    assert rows[0] == {"title": "Weather TLV", "url": "https://example.com/w", "snippet": "Sunny, 24°"} and rows[1]["url"] == "https://b.example/"


def test_the_whatsapp_tools_over_mcp_stdio(tmp_path):
    srv = tmp_path / "srv"
    srv.mkdir()
    (srv / "main.py").write_text(textwrap.dedent('''
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if "id" not in m:
                continue
            if m["method"] == "initialize":
                r = {"protocolVersion": "2025-06-18", "capabilities": {}}
            elif m["method"] == "tools/list":
                r = {"tools": [{"name": "get_status", "description": "status"}]}
            else:
                r = {"content": [{"type": "text", "text": "called " + m["params"]["name"] + " " + json.dumps(m["params"]["arguments"])}]}
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
    '''), encoding="utf-8")
    m = bd.McpStdio(str(srv), sys.executable)
    try:
        assert [t["name"] for t in m.tools()] == ["get_status"]
        assert m.call("get_status", {"x": 1}) == 'called get_status {"x": 1}'
    finally:
        m.proc.kill()


@pytest.fixture()
def provider(box):
    state = {"replies": [], "seen": [], "headers": []}

    class Up(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            state["seen"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            state["headers"].append(dict(self.headers.items()))
            code, body, hdr = state["replies"].pop(0)
            raw = json.dumps(body).encode()
            self.send_response(code)
            for k, v in hdr.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    p = {"upstream": {"base": f"http://127.0.0.1:{up.server_address[1]}/v1"}, "model": "m1"}
    yield p, state
    up.shutdown()


def _tool_call(cid, name, args):
    return {"choices": [{"message": {"content": "", "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5}}


def _answer(text):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 120, "completion_tokens": 7}}


def test_a_whole_exchange_contents_then_a_tool_then_the_answer(provider, box):
    p, state = provider
    bd.ensure_folders(["gemini"])
    bd.tool_write_file("notes.txt", "the answer is 42")
    state["replies"] = [(200, _tool_call("c1", "bridge_tools", {}), {}),
                        (200, _tool_call("c2", "bridge_call", {"name": "read_file", "args": {"path": "notes.txt"}}), {}),
                        (200, _tool_call("c3", "bridge_call", {"name": "read_file", "args": {"path": "notes.txt"}}), {}),
                        (200, _answer("42"), {})]
    seen_tools = []
    r = bd.run("gemini", p, "KEY", "m1", "RULES", "MESSAGE", on_tool=lambda t, a: seen_tools.append(t), mcp=FakeMcp())
    assert r["text"] == "42" and r["in"] == 420 and r["tools"] == ["read_file", "read_file"]
    first = state["seen"][0]
    assert first["messages"][0]["role"] == "system" and "RULES" in first["messages"][0]["content"] and "bridge_tools" in first["messages"][0]["content"]
    assert [t["function"]["name"] for t in first["tools"]] == ["bridge_tools", "bridge_call", "bridge_rules", "save_tool"], "only the small tools are sent"
    assert "get_status" in state["seen"][1]["messages"][-1]["content"], "the table of contents went back"
    assert "the answer is 42" in state["seen"][2]["messages"][-1]["content"]
    assert state["headers"][0]["Authorization"] == "Bearer KEY" and "yb-bridge" in state["headers"][0]["User-Agent"]
    prov = box / "agent" / "providers" / "gemini"
    log = (prov / "requests.log").read_text(encoding="utf-8").splitlines()
    assert sum('"status"' in l for l in log) == 4 and '"parts_chars"' in log[0], "4 requests, and what the first was made of"
    assert len((prov / "tools.log").read_text(encoding="utf-8").splitlines()) == 2 and seen_tools == ["read_file"], "the same call twice runs once"


def test_one_retry_only_when_the_provider_says_when(provider, monkeypatch):
    p, state = provider
    monkeypatch.setattr(bd.time, "sleep", lambda s: None)
    state["replies"] = [(429, {"error": {"message": "slow down"}}, {"Retry-After": "2"}), (200, _answer("ok"), {})]
    assert bd.run("gemini", p, "", "m1", "", "hi", mcp=FakeMcp())["text"] == "ok" and len(state["seen"]) == 2
    state["replies"] = [(429, {"error": {"message": "Resource has been exhausted"}}, {})]
    with pytest.raises(bd.DirectError) as e:
        bd.run("gemini", p, "", "m1", "", "hi", mcp=FakeMcp())
    assert e.value.code == 429 and len(state["seen"]) == 3, "no retry when the provider does not say when"
    state["replies"] = [(429, {"error": {"details": [{"retryDelay": "79188s"}]}}, {})]
    with pytest.raises(bd.DirectError):
        bd.run("gemini", p, "", "m1", "", "hi", mcp=FakeMcp())
    assert len(state["seen"]) == 4, "a daily quota (22 hours) is not waited for"


def test_the_rules_go_as_a_table_of_contents_and_a_rule_is_read_when_needed(provider, box):
    """Owner 21.11: like the tools — the owner's "כלל בסיס" rules and who this group is go in full; every other rule is one line of
    contents, read with bridge_rules when the provider needs it."""
    p, state = provider
    rules = "\n".join(["- *עברית תמיד (יקיר 14.11.2026, כלל בסיס לכל הגשר):* כל תשובה בעברית.",
                       "- *שמות, לא מספרים (יקיר 09.11.2026):* לא כותבים מספר טלפון.", "  המשך הכלל בשורה שנייה.", "",
                       "הקבוצה הנוכחית \"ויוי\".", "# הוראות לשיחה", "- *השם שלך: ויוי*"])
    always, items = bd.split_rules(rules)
    assert "עברית תמיד" in always and "הקבוצה הנוכחית" in always and [t for t, _ in items] == ["שמות, לא מספרים (יקיר 09.11.2026)", "הוראות לשיחה", "השם שלך: ויוי"]
    assert "המשך הכלל בשורה שנייה" in items[0][1]
    state["replies"] = [(200, _tool_call("c1", "bridge_rules", {"number": 1}), {}), (200, _answer("בסדר"), {})]
    assert bd.run("gemini", p, "", "m1", rules, "hi", mcp=FakeMcp())["text"] == "בסדר"
    system = state["seen"][0]["messages"][0]["content"]
    assert "כל תשובה בעברית" in system and "1. שמות, לא מספרים" in system and "לא כותבים מספר טלפון" not in system
    assert "לא כותבים מספר טלפון" in state["seen"][1]["messages"][-1]["content"], "the rule in full when asked"


def test_a_busy_model_is_asked_once_more_after_ten_seconds(provider, monkeypatch):
    """Owner 21.11 18:45: three 503 "high demand" on flash-lite passed when the owner sent again; the bridge asks once more itself."""
    p, state = provider
    waits = []
    monkeypatch.setattr(bd.time, "sleep", lambda s: waits.append(s))
    state["replies"] = [(503, {"error": {"message": "This model is currently experiencing high demand"}}, {}), (200, _answer("ok"), {})]
    assert bd.run("gemini", p, "", "m1", "", "hi", mcp=FakeMcp())["text"] == "ok" and waits == [bd.BUSY_RETRY_SEC]


def test_a_used_up_daily_quota_is_skipped_until_the_providers_own_reset(provider, box, monkeypatch):
    """Owner 21.11 18:45: gemini-3-flash 429 "requests per day, limit 20" four times; the model is never removed, only skipped until
    the time Google gave, and no second request is spent on it."""
    p, state = provider
    raw = {"error": {"code": 429, "message": "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, model: gemini-3-flash, "
                     "description: Generate content requests per day limit exceeded", "details": [{"retryDelay": "3600s"}]}}
    state["replies"] = [(429, raw, {})]
    with pytest.raises(bd.DirectError):
        bd.run("gemini", p, "", "m3", "", "hi", mcp=FakeMcp())
    assert len(state["seen"]) == 1 and 3500 < bd.exhausted_until("gemini", "m3") - __import__("time").time() <= 3600
    assert bd.exhausted_until("gemini", "m1") == 0
    state["replies"] = [(429, {"error": {"message": "Rate limit: tokens per minute"}}, {})]
    with pytest.raises(bd.DirectError):
        bd.run("gemini", p, "", "m1", "", "hi", mcp=FakeMcp())
    assert bd.exhausted_until("gemini", "m1") == 0, "a per-minute limit is not a daily quota"
