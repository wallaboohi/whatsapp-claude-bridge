"""tools/oai_proxy.py: Anthropic Messages <-> OpenAI Chat Completions, with a mock OpenAI server (no network, no real key)."""
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import oai_proxy as px  # noqa: E402

PROV = {"model": "gpt-4o", "upstream": {"base": "http://x/v1", "max_tokens_field": "max_completion_tokens"}}


def test_request_translation_keeps_tool_flow_in_order():
    body = {"model": "claude-sonnet-4", "max_tokens": 100, "stream": True, "system": [{"type": "text", "text": "SYS"}],
            "tools": [{"name": "Read", "description": "d", "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}}}],
            "tool_choice": {"type": "any"},
            "messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": "a"}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "DATA"}]},
                                                      {"type": "text", "text": "next"}]}]}
    r = px.to_openai(body, PROV)
    assert r["model"] == "gpt-4o" and r["max_completion_tokens"] == 100 and "max_tokens" not in r and r["stream"] and r["tool_choice"] == "required"
    assert [m["role"] for m in r["messages"]] == ["system", "user", "assistant", "tool", "user"]
    assert r["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"p": "a"}'
    assert r["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "DATA"} and r["messages"][4]["content"] == "next"
    assert r["tools"][0]["function"]["name"] == "Read" and r["tools"][0]["function"]["parameters"]["properties"]["p"]
    assert px.to_openai({"model": "qwen-x", "messages": []}, PROV)["model"] == "qwen-x"  # a real model name passes through


def test_image_becomes_data_url():
    r = px.to_openai({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}, {"type": "text", "text": "what"}]}]}, PROV)
    c = r["messages"][0]["content"]
    assert c[0]["image_url"]["url"] == "data:image/png;base64,QUJD" and c[1]["text"] == "what"


def test_response_translation():
    a = px.from_openai({"choices": [{"finish_reason": "tool_calls", "message": {"content": "x", "tool_calls": [
        {"id": "c1", "function": {"name": "Read", "arguments": '{"p": 1}'}}]}}], "usage": {"prompt_tokens": 5, "completion_tokens": 7}}, "claude-x")
    assert a["stop_reason"] == "tool_use" and a["content"][1] == {"type": "tool_use", "id": "c1", "name": "Read", "input": {"p": 1}}
    assert a["usage"] == {"input_tokens": 5, "output_tokens": 7}
    assert px.from_openai({"choices": [{"finish_reason": "length", "message": {"content": "t"}}]}, "m")["stop_reason"] == "max_tokens"


def _sse(*chunks):
    return [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]


def test_stream_text_then_tool_call_and_index_reuse():
    ch = lambda delta, fin=None: {"choices": [{"delta": delta, "finish_reason": fin}]}  # noqa: E731
    ev = list(px.stream_events(_sse(
        ch({"content": "he"}), ch({"content": "llo"}),
        ch({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "Read", "arguments": '{"p":'}}]}),
        ch({"tool_calls": [{"index": 0, "function": {"arguments": '1}'}}]}),
        ch({"tool_calls": [{"index": 0, "id": "b", "function": {"name": "Glob", "arguments": "{}"}}]}, "tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}}), "m"))
    names = [e for e, _ in ev]
    assert names[0] == "message_start" and names[-2:] == ["message_delta", "message_stop"]
    starts = [d["content_block"] for e, d in ev if e == "content_block_start"]
    assert [s["type"] for s in starts] == ["text", "tool_use", "tool_use"] and [s.get("id") for s in starts[1:]] == ["a", "b"]  # index 0 reused = a new call
    assert "".join(d["delta"]["partial_json"] for e, d in ev if e == "content_block_delta" and d["delta"]["type"] == "input_json_delta" and d["index"] == 1) == '{"p":1}'
    assert names.count("content_block_stop") == 3
    assert ev[-2][1]["delta"]["stop_reason"] == "tool_use" and ev[-2][1]["usage"]["output_tokens"] == 4
    empty = list(px.stream_events(_sse(), "m"))  # nothing came: still a valid message
    assert [e for e, _ in empty if e.startswith("content_block")] == ["content_block_start", "content_block_stop"]


@pytest.fixture()
def proxy(tmp_path, monkeypatch):
    seen = {}

    class Up(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            raw = json.dumps({"data": [{"id": "models/real-1"}, {"id": "real-2"}]} if not seen.get("fail") else {"error": "x"}).encode()
            self.send_response(401 if seen.get("fail") else 200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["req"] = req
            if seen.get("fail"):
                raw = json.dumps({"error": {"message": "bad key"}}).encode()
                self.send_response(401)
            elif req.get("stream"):
                raw = ("\n".join(_sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
                                      {"choices": [{"delta": {}, "finish_reason": "stop"}]})) + "\n").encode()
                self.send_response(200)
            else:
                raw = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "pong"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode()
                self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    (tmp_path / "agent").mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "t.key").write_text("SECRET-KEY", encoding="utf-8")
    (tmp_path / "agent" / "providers.json").write_text(json.dumps({"t": {"model": "gpt-4o", "key_file": "t.key", "models": ["gpt-4o"],
                                                                         "upstream": {"base": f"http://127.0.0.1:{up.server_address[1]}/v1"}}}), encoding="utf-8")
    monkeypatch.setattr(px, "ROOT", str(tmp_path))
    srv = px.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()
    up.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_http_non_stream_stream_and_errors(proxy):
    base, seen = proxy
    st, raw = _post(base + "/p/t/v1/messages", {"model": "claude-x", "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]})
    msg = json.loads(raw)
    assert st == 200 and msg["content"][0]["text"] == "pong" and seen["auth"] == "Bearer SECRET-KEY" and seen["req"]["model"] == "gpt-4o"
    assert "SECRET-KEY" not in raw
    st, raw = _post(base + "/p/t/v1/messages", {"model": "claude-x", "max_tokens": 5, "stream": True, "messages": [{"role": "user", "content": "ping"}]})
    assert st == 200 and "event: message_start" in raw and '"text": "hi"' in raw and raw.rstrip().endswith('"type": "message_stop"}')
    seen["fail"] = True
    st, raw = _post(base + "/p/t/v1/messages", {"model": "m", "max_tokens": 5, "messages": []})
    assert st == 401 and json.loads(raw)["error"] == {"type": "authentication_error", "message": "bad key"}
    assert _post(base + "/p/nope/v1/messages", {})[0] == 404
    assert json.loads(_post(base + "/p/t/v1/messages/count_tokens", {"messages": [{"role": "user", "content": "x" * 400}]})[1])["input_tokens"] >= 100


def test_models_come_from_the_provider_and_prove_the_key(proxy):
    base, seen = proxy
    got = json.loads(urllib.request.urlopen(base + "/p/t/v1/models", timeout=10).read())
    assert [m["id"] for m in got["data"]] == ["real-1", "real-2"] and seen["auth"] == "Bearer SECRET-KEY"
    seen["fail"] = True
    try:
        urllib.request.urlopen(base + "/p/t/v1/models", timeout=10)
        assert False, "a rejected key must not look connected"
    except urllib.error.HTTPError as e:
        assert e.code == 401


def test_upstream_error_shapes():
    assert px.upstream_message('{"error": {"message": "bad key"}}') == "bad key"
    assert px.upstream_message('[{"error": {"code": 404, "message": "model not found"}}]') == "model not found"  # Gemini sends a list
    assert px.upstream_message('{"error": "plain"}') == "plain" and px.upstream_message("not json", "x") == "not json"
