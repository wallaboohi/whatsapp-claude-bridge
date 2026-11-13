"""Anthropic Messages API -> OpenAI Chat Completions translator (owner 13.11).

The bridge's engine is Claude Code, which speaks only the Anthropic protocol. This small local server lets any provider that speaks
the OpenAI protocol (OpenAI, Gemini's OpenAI endpoint, Groq, Mistral, LM Studio, ...) sit behind that engine: text, system prompts,
images, tool calls and streaming are translated both ways.

  POST /p/<provider>/v1/messages               Anthropic request in, Anthropic response (or SSE stream) out
  POST /p/<provider>/v1/messages/count_tokens  rough estimate
  GET  /p/<provider>/v1/models                 the provider's models from providers.json
  GET  /health

<provider> is a key of agent/providers.json with an "upstream" object: {"base": "https://api.openai.com/v1",
"max_tokens_field": "max_tokens" | "max_completion_tokens", "model_map": {"claude-...": "real-model"}}. The API key is read from
secrets/<key_file> on every request (never logged, never returned). Listens on 127.0.0.1 only.

  uv run python tools/oai_proxy.py [--port 8799]
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8799
UA = "oai-proxy/1.0 (whatsapp-claude-bridge)"  # Cloudflare-fronted providers (Groq) answer 403 "error 1010" to the default Python-urllib agent
STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "function_call": "tool_use", "content_filter": "end_turn"}


def load_providers():
    try:
        out = json.load(open(os.path.join(ROOT, "agent", "providers.json"), encoding="utf-8"))
    except (OSError, ValueError):
        out = {}
    try:
        cfg = json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))
        for k, v in (cfg.get("providers") or {}).items():
            out.setdefault(k, {}).update(v)
    except (OSError, ValueError):
        pass
    return out


def read_key(p):
    if not p.get("key_file"):
        return ""
    try:
        return open(os.path.join(ROOT, "secrets", p["key_file"]), encoding="utf-8").read().strip()
    except OSError:
        return ""


# ---------------- request: Anthropic -> OpenAI ----------------

def _blocks(content):
    return [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])


def _text(content):
    return "".join(b.get("text", "") for b in _blocks(content) if b.get("type") == "text")


def _result_text(b):
    t = _text(b.get("content"))
    return ("Error: " + t) if b.get("is_error") else t


def _image_url(b):
    s = b.get("source") or {}
    if s.get("type") == "base64":
        return f"data:{s.get('media_type', 'image/png')};base64,{s.get('data', '')}"
    return s.get("url", "")


def to_openai(body, provider):
    """The OpenAI chat-completions request for an Anthropic messages request."""
    up = provider.get("upstream") or {}
    model = body.get("model") or ""
    model = (up.get("model_map") or {}).get(model) or (provider.get("model") if model.startswith("claude") or not model else model)
    msgs = []
    sysx = _text(body.get("system"))
    if sysx:
        msgs.append({"role": "system", "content": sysx})
    for m in body.get("messages") or []:
        c = m.get("content")
        if m.get("role") == "assistant":
            calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in _blocks(c) if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": _text(c) or None}
            if calls:
                msg["tool_calls"] = calls
            if msg["content"] or calls:
                msgs.append(msg)
            continue
        results = [{"role": "tool", "tool_call_id": b["tool_use_id"], "content": _result_text(b)} for b in _blocks(c) if b.get("type") == "tool_result"]
        parts = []
        for b in _blocks(c):
            if b.get("type") == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif b.get("type") == "image" and _image_url(b):
                parts.append({"type": "image_url", "image_url": {"url": _image_url(b)}})
        msgs += results  # a tool result must follow the assistant message that called the tool
        if parts:
            msgs.append({"role": "user", "content": parts if any(p["type"] == "image_url" for p in parts) else "".join(p["text"] for p in parts)})
    req = {"model": model, "messages": msgs}
    req[up.get("max_tokens_field") or "max_tokens"] = int(body.get("max_tokens") or 4096)
    for k_in, k_out in (("temperature", "temperature"), ("top_p", "top_p")):
        if body.get(k_in) is not None:
            req[k_out] = body[k_in]
    if body.get("stop_sequences"):
        req["stop"] = body["stop_sequences"][:4]
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
             for t in body.get("tools") or [] if t.get("name")]
    if tools:
        req["tools"] = tools
        tc = body.get("tool_choice") or {}
        if tc.get("type") == "any":
            req["tool_choice"] = "required"
        elif tc.get("type") == "tool" and tc.get("name"):
            req["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif tc.get("type") == "none":
            req["tool_choice"] = "none"
    if body.get("stream"):
        req["stream"] = True
        req["stream_options"] = {"include_usage": True}
    return req


# ---------------- response: OpenAI -> Anthropic ----------------

def _args(s):
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except ValueError:
        return {}


def _stop(finish, has_tools):
    s = STOP.get(finish, "end_turn")
    return "tool_use" if has_tools and s == "end_turn" else s


def from_openai(resp, model):
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    calls = msg.get("tool_calls") or []
    for tc in calls:
        fn = tc.get("function") or {}
        content.append({"type": "tool_use", "id": tc.get("id") or "toolu_" + uuid.uuid4().hex[:20], "name": fn.get("name", ""), "input": _args(fn.get("arguments"))})
    if not content:
        content = [{"type": "text", "text": ""}]
    u = resp.get("usage") or {}
    return {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": _stop(ch.get("finish_reason"), bool(calls)), "stop_sequence": None,
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}}


def stream_events(lines, model):
    """Anthropic stream events (event, data) from an iterable of OpenAI SSE lines."""
    yield "message_start", {"type": "message_start", "message": {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant", "model": model,
                                                                  "content": [], "stop_reason": None, "stop_sequence": None,
                                                                  "usage": {"input_tokens": 0, "output_tokens": 0}}}
    idx, open_kind, tools, finish, usage = -1, None, {}, None, {}  # tools: (upstream index) -> (anthropic index, call id)

    def close():
        nonlocal open_kind
        if open_kind is not None:
            open_kind = None
            return [("content_block_stop", {"type": "content_block_stop", "index": idx})]
        return []

    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            ch = json.loads(data)
        except ValueError:
            continue
        if ch.get("usage"):
            usage = ch["usage"]
        for choice in ch.get("choices") or []:
            d = choice.get("delta") or {}
            if d.get("content"):
                if open_kind != "text":
                    yield from close()
                    idx += 1
                    open_kind = "text"
                    yield "content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}}
                yield "content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": d["content"]}}
            for pos, tc in enumerate(d.get("tool_calls") or []):
                ui, cid = tc.get("index", pos), tc.get("id")
                known = tools.get(ui)
                if known is None or (cid and known[1] and cid != known[1]):  # a new call (some providers reuse index 0 for every call)
                    yield from close()
                    idx += 1
                    cid = cid or "toolu_" + uuid.uuid4().hex[:20]
                    tools[ui] = (idx, cid)
                    open_kind = "tool"
                    yield "content_block_start", {"type": "content_block_start", "index": idx,
                                                  "content_block": {"type": "tool_use", "id": cid, "name": (tc.get("function") or {}).get("name", ""), "input": {}}}
                args = (tc.get("function") or {}).get("arguments")
                if args:
                    yield "content_block_delta", {"type": "content_block_delta", "index": tools[ui][0], "delta": {"type": "input_json_delta", "partial_json": args}}
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    yield from close()
    if idx == -1:  # nothing came: an empty text block keeps the message valid
        yield "content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
        yield "content_block_stop", {"type": "content_block_stop", "index": 0}
    yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": _stop(finish, bool(tools)), "stop_sequence": None},
                            "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}}
    yield "message_stop", {"type": "message_stop"}


def upstream_message(raw, fallback=""):
    """The human message of an upstream error body: a dict, a one-item list (Gemini) or plain text."""
    try:
        v = json.loads(raw)
    except ValueError:
        return (raw or str(fallback))[:300]
    if isinstance(v, list) and v:
        v = v[0]
    if isinstance(v, dict):
        e = v.get("error", v)
        if isinstance(e, dict):
            return str(e.get("message") or json.dumps(e, ensure_ascii=False))[:300]
        return str(e or v.get("message") or fallback)[:300]
    return str(v or fallback)[:300]


def error_body(status, msg):
    kind = {400: "invalid_request_error", 401: "authentication_error", 402: "billing_error", 403: "permission_error", 404: "not_found_error",
            429: "rate_limit_error"}.get(status, "api_error")
    return {"type": "error", "error": {"type": kind, "message": str(msg)[:400]}}


# ---------------- server ----------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if os.environ.get("OAI_PROXY_DEBUG"):  # request line and status only: never a body or a key
            print(time.strftime("%H:%M:%S"), fmt % a, flush=True)

    def _json(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self):
        parts = [x for x in self.path.split("?")[0].split("/") if x]
        if len(parts) >= 3 and parts[0] == "p":
            return parts[1], "/".join(parts[2:])
        return None, "/".join(parts)

    def do_GET(self):
        name, rest = self._route()
        if rest == "health":
            return self._json(200, {"ok": True})
        p = load_providers().get(name or "")
        if not p or not p.get("upstream"):
            return self._json(404, error_body(404, "unknown provider"))
        if rest == "v1/models":  # the provider's real list (this also proves the key); the static list from providers.json when it cannot be read
            try:
                r = urllib.request.urlopen(urllib.request.Request(p["upstream"]["base"].rstrip("/") + "/models",
                                                                  headers={"Authorization": "Bearer " + read_key(p), "User-Agent": UA}), timeout=15)
                ids = [str(m.get("id", "")).replace("models/", "") for m in (json.loads(r.read().decode("utf-8", "replace")).get("data") or []) if m.get("id")]
                if ids:
                    return self._json(200, {"data": [{"id": m, "type": "model"} for m in ids]})
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return self._json(e.code, error_body(e.code, "the provider rejected the key"))
            except Exception:
                pass
            return self._json(200, {"data": [{"id": m, "type": "model"} for m in p.get("models") or [p.get("model")]]})
        self._json(404, error_body(404, "not found"))

    def do_POST(self):
        try:
            self._post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # never drop the connection without an answer: Claude Code would retry blindly
            try:
                self._json(502, error_body(502, f"translator error: {e}"))
            except Exception:
                pass

    def _post(self):
        name, rest = self._route()
        p = load_providers().get(name or "")
        if not p or not p.get("upstream"):
            return self._json(404, error_body(404, "unknown provider (needs an upstream in providers.json)"))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json(400, error_body(400, "bad json"))
        if rest == "v1/messages/count_tokens":
            return self._json(200, {"input_tokens": max(1, len(json.dumps(body, ensure_ascii=False)) // 4)})
        if rest != "v1/messages":
            return self._json(404, error_body(404, "not found"))
        req = to_openai(body, p)
        if os.environ.get("OAI_PROXY_DEBUG"):  # shape of the conversation only: roles, and the first characters of a tool result
            print("   <-", [(m["role"], (m.get("content") or "")[:60] if m["role"] == "tool" else len(str(m.get("content") or ""))) for m in req["messages"]][-4:], flush=True)
        key = read_key(p)
        url = p["upstream"]["base"].rstrip("/") + "/chat/completions"
        hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": UA}
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(req).encode(), headers=hdr, method="POST"), timeout=600)
        except urllib.error.HTTPError as e:
            return self._json(e.code, error_body(e.code, upstream_message(e.read().decode("utf-8", "replace"), e.reason)))
        except Exception as e:
            return self._json(502, error_body(502, f"upstream unreachable: {e}"))
        model = body.get("model") or req["model"]
        if not body.get("stream"):
            return self._json(200, from_openai(json.loads(r.read().decode("utf-8", "replace")), model))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            seen = []
            for ev, data in stream_events((raw.decode("utf-8", "replace") for raw in r), model):
                self.wfile.write(f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                if os.environ.get("OAI_PROXY_DEBUG") and ev in ("content_block_start", "message_delta"):
                    seen.append((data.get("content_block") or {}).get("name") or (data.get("delta") or {}).get("stop_reason") or (data.get("content_block") or {}).get("type"))
            if seen:
                print("   ->", seen, flush=True)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def make_server(port=PORT):
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port=PORT):
    make_server(port).serve_forever()


def start_in_thread(port=PORT):
    """For the agent: the proxy as a daemon thread. Returns the server (or None when the port is taken: another copy already serves)."""
    try:
        srv = make_server(port)
    except OSError:
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="oai-proxy").start()
    return srv


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else PORT
    print(f"oai_proxy on 127.0.0.1:{port}", flush=True)
    serve(port)
