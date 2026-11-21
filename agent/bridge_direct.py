"""The bridge talks to an AI provider by itself (owner 21.11, OWNER-SPEC.md sections 1-5, 14).

No Claude Code and no translator in between: the bridge sends the provider a short connection message (what the bridge is + a table of
contents of its tools), the bridge rules, the last messages and the current message, in the provider's own (OpenAI-style) format.
The provider gets three small tools and reaches every bridge tool through them:
  bridge_tools  - without a name: the table of contents; with a name: that tool's full definition
  bridge_call   - run a tool by name
  save_tool     - save a tool the provider downloaded or wrote into its own toolbox, to use it next time
The tools belong to the bridge and never depend on a provider: the WhatsApp tools (the bridge's MCP server, spoken to over stdio),
files / commands / web search / web pages, and each provider's own toolbox (agent/providers/<provider>/tools/<tool>/).
Every request and every tool use is logged per provider (agent/providers/<provider>/requests.log, tools.log).
This module imports nothing from agent.py; agent.py calls run() and turns DirectError into its own provider errors."""
import fnmatch
import glob
import html
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROVIDERS_DIR = os.path.join(HERE, "providers")
SHARED = "shared"
CLAUDE_HOME_PROVIDERS = ("claude", "claude_work")  # owner 21.11 12:37: providers 1 and 12 stay where they are (agent/home)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) yb-bridge/1.0"  # Groq answers 403 (error 1010) to a request without one
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_ROUNDS = 12
OUT_CAP = 20000
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".claude"}


class DirectError(Exception):
    """A provider refused or failed; `raw` is its own text (for the agent's classify_down), `code` the HTTP status (0 = no answer)."""
    def __init__(self, code, raw):
        super().__init__(f"{code}: {raw[:200]}")
        self.code, self.raw = code, raw


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append(path, row):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(name or ""))[:60]


# ---------------------------------------------------------------- folders and the table of contents

def provider_dir(name):
    if name in CLAUDE_HOME_PROVIDERS:
        return os.path.join(HERE, "home")
    return os.path.join(PROVIDERS_DIR, _safe_name(name))


def toolbox_dir(name):
    return os.path.join(PROVIDERS_DIR, SHARED if name == SHARED else _safe_name(name), "tools")


def ensure_folders(provider_names):
    """A folder for every provider (its files, its toolbox, its logs), the shared toolbox, and INDEX.md. Providers 1 and 12 keep
    agent/home. Never deletes or moves anything; returns the folders it created."""
    made = []
    for name in list(provider_names) + [SHARED]:
        if name in CLAUDE_HOME_PROVIDERS or name == "auto":
            continue
        tb = toolbox_dir(name)
        if not os.path.isdir(tb):
            os.makedirs(tb, exist_ok=True)
            made.append(os.path.dirname(tb))
        readme = os.path.join(os.path.dirname(tb), "README.md")
        if name != SHARED and not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n\nהקבצים וההוראות של הספק הזה בגשר. ארגז הכלים שלו: `tools/`. "
                        "השימוש בכלים: `tools.log`. הבקשות: `requests.log`.\n")
    write_index(provider_names)
    return made


def _box_tools(box):
    out = []
    for meta in sorted(glob.glob(os.path.join(box, "*", "tool.json"))):
        try:
            d = json.load(open(meta, encoding="utf-8"))
            d["_dir"] = os.path.dirname(meta)
            out.append(d)
        except (OSError, ValueError):
            continue
    return out


def write_index(provider_names):
    """agent/providers/INDEX.md: every provider's folder, its tools and where it writes; written by the bridge, not by hand."""
    lines = ["# תוכן העניינים של ארגזי הכלים בגשר", "", f"_נכתב אוטומטית על ידי הגשר ({_now()}). לא לערוך ביד._", "",
             "כלי הגשר (שייכים לגשר, לא לספק): כלי הווצאפ (שרת ה-MCP של הגשר), קבצים ופקודות, חיפוש ברשת וקריאת דף. "
             "ספק מגיע אליהם דרך `bridge_tools` / `bridge_call` ושומר כלי משלו דרך `save_tool`.", "",
             "| ספק | תיקייה | כלים משלו | כותב ל |", "|---|---|---|---|"]
    for name in list(provider_names) + [SHARED]:
        if name == "auto":
            continue
        if name in CLAUDE_HOME_PROVIDERS:
            lines.append(f"| {name} | `agent/home` (נשאר במקומו) | — | `agent/home` |")
            continue
        rel = os.path.relpath(os.path.dirname(toolbox_dir(name)), ROOT).replace("\\", "/")
        tools = ", ".join(t.get("name", "?") for t in _box_tools(toolbox_dir(name))) or "—"
        lines.append(f"| {name} | `{rel}` | {tools} | `{rel}/tools/`, `{rel}/tools.log`, `{rel}/requests.log` |")
    try:
        os.makedirs(PROVIDERS_DIR, exist_ok=True)
        with open(os.path.join(PROVIDERS_DIR, "INDEX.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- the WhatsApp tools: the bridge's MCP server over stdio

class McpStdio:
    """The bridge's own WhatsApp tool server, spoken to with plain JSON-RPC over stdio (no packages). One process, restarted when it dies."""

    def __init__(self, server_dir=None, python=None):
        self.dir = server_dir or os.path.join(ROOT, "whatsapp-mcp-server")
        self.python = python or os.path.join(self.dir, ".venv", "Scripts", "python.exe")
        self.proc, self.lines, self.lock, self.n, self._tools = None, None, threading.Lock(), 0, None

    def _start(self):
        self.proc = subprocess.Popen([self.python, "main.py"], cwd=self.dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=NO_WINDOW)
        self.lines = queue.Queue()

        def pump(p, q):
            for line in p.stdout:
                q.put(line)
            q.put(None)
        threading.Thread(target=pump, args=(self.proc, self.lines), daemon=True).start()
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "yb-bridge", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method, params, timeout=60):
        self.n += 1
        want = self.n
        self._send({"jsonrpc": "2.0", "id": want, "method": method, "params": params})
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = self.lines.get(timeout=max(0.1, end - time.time()))
            except queue.Empty:
                break
            if line is None:
                raise RuntimeError("the WhatsApp tool server closed")
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("id") == want:
                return r
        raise RuntimeError(f"the WhatsApp tool server did not answer {method} in {timeout} s")

    def _ready(self):
        if not self.proc or self.proc.poll() is not None:
            self._tools = None
            self._start()

    def tools(self):
        with self.lock:
            if self._tools is None:
                self._ready()
                self._tools = (self._rpc("tools/list", {}).get("result") or {}).get("tools") or []
            return self._tools

    def call(self, name, args, timeout=120):
        with self.lock:
            self._ready()
            r = self._rpc("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        if r.get("error"):
            return "שגיאה: " + json.dumps(r["error"], ensure_ascii=False)
        parts = (r.get("result") or {}).get("content") or []
        return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict)) or json.dumps(r.get("result"), ensure_ascii=False)


MCP = McpStdio()


# ---------------------------------------------------------------- the bridge's general tools (like Claude Code's, owned by the bridge)

def _path(p):
    p = os.path.expandvars(os.path.expanduser(str(p or "")))
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def tool_run_command(command, timeout=120):
    t = max(5, min(int(timeout or 120), 600))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=t, creationflags=NO_WINDOW)
    return (f"exit {r.returncode}\n" + (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else ""))[:OUT_CAP]


def tool_read_file(path, offset=0, limit=400):
    with open(_path(path), encoding="utf-8", errors="replace") as f:
        rows = f.read().splitlines()
    o, n = max(0, int(offset or 0)), max(1, min(int(limit or 400), 2000))
    return ("\n".join(f"{i + 1}\t{l}" for i, l in enumerate(rows[o:o + n], start=o)) + (f"\n… ({len(rows)} lines)" if len(rows) > o + n else ""))[:OUT_CAP * 2]


def tool_write_file(path, content):
    p = _path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content or "")
    return f"written {len(content or '')} chars to {p}"


def tool_edit_file(path, old, new, replace_all=False):
    p = _path(path)
    s = open(p, encoding="utf-8").read()
    n = s.count(old or "")
    if not old or n == 0:
        return "not found: the old text is not in the file"
    if n > 1 and not replace_all:
        return f"the old text appears {n} times; give more context or replace_all=true"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s.replace(old, new or "") if replace_all else s.replace(old, new or "", 1))
    return f"edited {p} ({n if replace_all else 1} place)"


def _walk(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            yield os.path.join(d, f)


def tool_list_files(pattern="*", path=""):
    root = _path(path) if path else ROOT
    out = [os.path.relpath(f, root).replace("\\", "/") for f in _walk(root) if fnmatch.fnmatch(os.path.basename(f), pattern or "*")
           or fnmatch.fnmatch(os.path.relpath(f, root).replace("\\", "/"), pattern or "*")]
    return "\n".join(out[:300]) + (f"\n… ({len(out)} files)" if len(out) > 300 else "")


def tool_search_text(pattern, path="", file_pattern="*"):
    root = _path(path) if path else ROOT
    rx = re.compile(pattern)
    hits = []
    for f in _walk(root):
        if not fnmatch.fnmatch(os.path.basename(f), file_pattern or "*") or os.path.getsize(f) > 3_000_000:
            continue
        try:
            for i, line in enumerate(open(f, encoding="utf-8", errors="ignore"), 1):
                if rx.search(line):
                    hits.append(f"{os.path.relpath(f, root)}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n… (first 200)"
        except OSError:
            continue
    return "\n".join(hits) or "no match"


def parse_ddg(page, n=8):
    out = []
    blocks = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', page, re.S)
    for u, t, rest in blocks[:n]:
        u = urllib.parse.unquote(re.sub(r"^.*?uddg=([^&]+).*$", r"\1", html.unescape(u)))
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        out.append({"title": html.unescape(re.sub("<.*?>", "", t)).strip(), "url": u,
                    "snippet": html.unescape(re.sub("<.*?>", "", sn.group(1))).strip() if sn else ""})
    return out


def tool_web_search(query, max_results=8):
    """A keyless web search (DuckDuckGo): owner 21.11 "get the bridge a web search tool"."""
    q = urllib.parse.urlencode({"q": query})
    page = urllib.request.urlopen(urllib.request.Request("https://html.duckduckgo.com/html/?" + q, headers={"User-Agent": UA}),
                                  timeout=20).read().decode("utf-8", "replace")
    rows = parse_ddg(page, int(max_results or 8))
    return json.dumps(rows, ensure_ascii=False) if rows else "no results"


def tool_fetch_url(url, max_chars=OUT_CAP):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read(3_000_000)
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:int(max_chars or OUT_CAP)]


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


S = {"type": "string"}
BRIDGE_TOOLS = {
    "run_command": (tool_run_command, "Run a PowerShell command on the bridge computer (in the bridge folder) and return its output.",
                    _schema({"command": S, "timeout": {"type": "integer"}}, ["command"])),
    "read_file": (tool_read_file, "Read a text file (path absolute or relative to the bridge folder), with line numbers.",
                  _schema({"path": S, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"])),
    "write_file": (tool_write_file, "Write a whole text file (creates folders).", _schema({"path": S, "content": S}, ["path", "content"])),
    "edit_file": (tool_edit_file, "Replace exact text in a file.", _schema({"path": S, "old": S, "new": S, "replace_all": {"type": "boolean"}}, ["path", "old", "new"])),
    "list_files": (tool_list_files, "List files by a name pattern (e.g. *.py) under a folder.", _schema({"pattern": S, "path": S})),
    "search_text": (tool_search_text, "Search file contents with a regular expression.", _schema({"pattern": S, "path": S, "file_pattern": S}, ["pattern"])),
    "fetch_url": (tool_fetch_url, "Read a web page as plain text.", _schema({"url": S, "max_chars": {"type": "integer"}}, ["url"])),
}


# ---------------------------------------------------------------- the provider's own toolbox

def save_tool(provider, name, description, code, parameters=None, shared=False):
    """A tool the provider downloaded or wrote: tools/<name>/tool.json + main.py (reads its arguments as JSON on stdin, prints the result)."""
    tname = _safe_name(name)
    if not tname or not code:
        return "a tool needs a name and code"
    d = os.path.join(toolbox_dir(SHARED if shared else provider), tname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "main.py"), "w", encoding="utf-8") as f:
        f.write(code)
    meta = {"name": tname, "description": description or "", "parameters": parameters or {"type": "object", "properties": {}},
            "run": "python main.py", "by": provider, "saved": _now()}
    with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return f"saved: {tname} in {os.path.relpath(d, ROOT)}"


def _own_tool(provider, name):
    for box in (toolbox_dir(provider), toolbox_dir(SHARED)):
        meta = os.path.join(box, _safe_name(name), "tool.json")
        if os.path.exists(meta):
            return os.path.dirname(meta), json.load(open(meta, encoding="utf-8"))
    return None, None


def run_own_tool(d, args, timeout=120):
    r = subprocess.run([sys.executable, "main.py"], cwd=d, input=json.dumps(args or {}, ensure_ascii=False), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    return ((r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.returncode and r.stderr else ""))[:OUT_CAP] or f"exit {r.returncode}"


# ---------------------------------------------------------------- what the provider sees

def _first_line(s):
    return (s or "").strip().splitlines()[0][:140] if (s or "").strip() else ""


def contents(provider, mcp=None):
    m = mcp or MCP
    rows = ["## כלי הווצאפ של הגשר"]
    try:
        rows += [f"- {t['name']}: {_first_line(t.get('description'))}" for t in m.tools() if t["name"] not in BRIDGE_TOOLS]
    except Exception as e:
        rows.append(f"- (שרת כלי הווצאפ לא זמין כרגע: {str(e)[:80]})")
    rows += ["## כלים כלליים של הגשר"] + [f"- {n}: {v[1]}" for n, v in BRIDGE_TOOLS.items()]
    own = _box_tools(toolbox_dir(provider))
    shared = _box_tools(toolbox_dir(SHARED))
    rows += ["## הכלים שלך (ארגז הכלים שלך)"] + ([f"- {t['name']}: {t.get('description', '')}" for t in own] or ["- (עדיין אין)"])
    if shared:
        rows += ["## כלים משותפים"] + [f"- {t['name']}: {t.get('description', '')}" for t in shared]
    return "\n".join(rows)


def definition(provider, name, mcp=None):
    if name in BRIDGE_TOOLS:
        return json.dumps({"name": name, "description": BRIDGE_TOOLS[name][1], "parameters": BRIDGE_TOOLS[name][2]}, ensure_ascii=False)
    d, meta = _own_tool(provider, name)
    if meta:
        return json.dumps(meta, ensure_ascii=False)
    for t in (mcp or MCP).tools():
        if t["name"] == name:
            return json.dumps({"name": name, "description": t.get("description"), "parameters": t.get("inputSchema")}, ensure_ascii=False)[:6000]
    return f"אין כלי בשם {name}. לרשימה: bridge_tools בלי שם."


def call_tool(provider, name, args, mcp=None):
    if name in BRIDGE_TOOLS:
        return str(BRIDGE_TOOLS[name][0](**(args or {})))
    d, meta = _own_tool(provider, name)
    if meta:
        return run_own_tool(d, args)
    m = mcp or MCP
    if any(t["name"] == name for t in m.tools()):
        return m.call(name, args)
    return f"אין כלי בשם {name}. לרשימה: bridge_tools בלי שם."


RULE_HEAD = re.compile(r"^\s*[-•]\s*\*([^*]{2,160})\*")
BASE_MARK = "כלל בסיס"


def split_rules(text):
    """Owner 21.11: the bridge rules like the tools — a table of contents, and a rule is read when it is needed. Returns
    (always, items): `always` = the rules the owner marked "כלל בסיס" + the lines that are not a numbered rule (who this group is,
    its purpose); `items` = [(title, full text)] for the table of contents."""
    always, items, cur = [], [], None
    for line in (text or "").splitlines():
        m = RULE_HEAD.match(line)
        if m:
            cur = [m.group(1).strip().rstrip(":"), [line]]
            items.append(cur)
        elif line.lstrip().startswith("#"):
            cur = [line.strip("# ").strip(), [line]]
            items.append(cur)
        elif not line.strip():
            cur = None  # a blank line ends a rule: the next paragraph is not part of it
        elif cur is not None:
            cur[1].append(line)
        else:
            always.append(line)
    out = []
    for title, body in items:
        if BASE_MARK in title:
            always.append("\n".join(body))
        else:
            out.append((title, "\n".join(body)))
    return "\n".join(always).strip(), out


def rules_file(chat, items):
    """The full text of every rule in the contents, numbered, in a file a CLI provider reads with its own file tool."""
    d = os.path.join(PROVIDERS_DIR, "_rules")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, re.sub(r"[^0-9A-Za-z]+", "_", chat or "chat")[:40] + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# כללי הגשר לשיחה הזו (נכתב על ידי הגשר)\n\n" + "\n\n".join(f"## {i}. {t}\n{b}" for i, (t, b) in enumerate(items, 1)) + "\n")
    return path


def cli_rules_text(name, chat, rules):
    """Owner 21.11 19:05: a CLI provider gets the bridge's way like the others: what the bridge is, where its toolbox is, the rules the
    owner marked "כלל בסיס" in full, every other rule as one line of contents — and the full text in a file it reads when needed."""
    always, items = split_rules(rules)
    rel = os.path.relpath(os.path.dirname(toolbox_dir(name)), ROOT).replace("\\", "/")
    intro = (f"אתה בינה שמתארחת בגשר ווצאפ של יקיר (YB גשר AI לווצאפ). הגשר הוא תוכנה עצמאית: הוא מקבל את ההודעות ומפרסם את התשובה שלך. "
             f"כלי הווצאפ של הגשר זמינים לך דרך שרת ה-MCP 'whatsapp'. ארגז הכלים שלך בגשר: {rel}/tools/ (כלי שהורדת או כתבת — שמור שם; "
             f"תוכן כל ארגזי הכלים: agent/providers/INDEX.md). התשובה הסופית שלך נשלחת לקבוצה אוטומטית.")
    if not items:
        return intro + "\n\n" + always
    path = rules_file(chat, items)
    return (intro + "\n\n" + always + f"\n\nתוכן עניינים של שאר כללי הגשר. לפני פעולה שנוגעת לכלל, קרא את הסעיף בקובץ {path}:\n"
            + rules_contents(items))


def rules_contents(items):
    return "\n".join(f"{i}. {t}" for i, (t, _) in enumerate(items, 1))


META_TOOLS = [
    {"type": "function", "function": {"name": "bridge_tools", "description": "Without a name: the table of contents of every bridge tool (one line each). With a name: that tool's full definition.",
                                      "parameters": _schema({"name": S})}},
    {"type": "function", "function": {"name": "bridge_call", "description": "Run a bridge tool by name with its arguments (an object).",
                                      "parameters": _schema({"name": S, "args": {"type": "object"}}, ["name"])}},
    {"type": "function", "function": {"name": "bridge_rules", "description": "The bridge rules: without a number their table of contents; with a number that rule in full. Read a rule before an action it covers.",
                                      "parameters": _schema({"number": {"type": "integer"}})}},
    {"type": "function", "function": {"name": "save_tool", "description": "Save a tool you downloaded or wrote into your own toolbox, to use it next time with bridge_call: Python code that reads its arguments as JSON on stdin and prints the result.",
                                      "parameters": _schema({"name": S, "description": S, "code": S, "parameters": {"type": "object"}, "shared": {"type": "boolean"}}, ["name", "description", "code"])}},
]


def connection_text(provider):
    rel = os.path.relpath(os.path.dirname(toolbox_dir(provider)), ROOT).replace("\\", "/")
    return (f"אתה בינה שמתארחת בגשר ווצאפ של יקיר (YB גשר AI לווצאפ). הגשר הוא תוכנה עצמאית: הוא מקבל את ההודעות, שולח לך אותן "
            f"ומפרסם את התשובה שלך בווצאפ. לגשר יש כלים משלו (ווצאפ, קבצים, פקודות, חיפוש ברשת וקריאת דפים) ואתה מפעיל אותם דרכו: "
            f"bridge_tools בלי שם מחזיר את תוכן העניינים, bridge_tools עם שם מחזיר הגדרה מלאה, bridge_call מפעיל כלי. "
            f"כלי שאין בגשר או שלא עובד לך — הורד או כתוב אותו ושמור עם save_tool; הוא יישמר בארגז הכלים שלך ({rel}/tools/). "
            f"כללי הגשר: הכללים המסומנים 'כלל בסיס' מופיעים כאן במלואם; לשאר יש תוכן עניינים — לפני פעולה שנוגעת לכלל, קרא אותו עם "
            f"bridge_rules ומספר. התשובה הסופית שלך נשלחת לקבוצה אוטומטית.")


# ---------------------------------------------------------------- one run against a provider

def _base(p):
    up = p.get("upstream") or {}
    base = (up.get("base") or p.get("base_url") or "").rstrip("/")
    if "{account_id}" in base:
        try:
            acc = open(os.path.join(ROOT, "secrets", up.get("account_file") or ""), encoding="utf-8").read().strip()
        except OSError:
            acc = "MISSING-ACCOUNT-ID"
        base = base.replace("{account_id}", acc)
    return base


def keys_for(p, token=""):
    """Every account key of a provider, in order: secrets/<key_file>, then <key_file>2, 3 ... (owner 21.11: "add the keys"; one
    account's used-up quota or refused key moves the request to the next account instead of stopping)."""
    kf = p.get("key_file")
    out = []
    if kf:
        for i in range(1, 10):
            path = os.path.join(ROOT, "secrets", kf + ("" if i == 1 else str(i)))
            try:
                k = open(path, encoding="utf-8").read().strip()
            except OSError:
                continue
            if k and k not in out:
                out.append(k)
    if token and token not in out:
        out.append(token)
    return out or [""]


def _key_moves_on(code, raw):
    """True when another account may answer: the key refused (401/403), no balance (402), or a daily quota used up."""
    return code in (401, 402, 403) or (code == 429 and _daily_until(raw) is not None)


def _retry_seconds(headers, body):
    ra = (headers or {}).get("retry-after") or (headers or {}).get("Retry-After")
    if ra and str(ra).strip().isdigit():
        return int(ra)
    m = re.search(r'"retryDelay"\s*:\s*"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?"', body or "")
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(float(m.group(3) or 0))
    m = re.search(r"(?i)(?:try again|retry) in ([\d.]+)\s*s", body or "")
    return int(float(m.group(1))) if m else None


BUSY_RETRY_SEC = 10  # owner 21.11 18:45: a 503 "high demand" passes a little later; one more try after this many seconds


def _daily_until(raw):
    """When a used-up DAILY quota comes back, from the provider's own words (quotaId ...PerDay / "per day" + retryDelay / "retry in");
    None for anything else (a per-minute limit, a busy model)."""
    if not re.search(r"(?i)PerDay|per day|requests per day|daily", raw or ""):
        return None
    secs = _retry_seconds({}, raw)
    if secs is None:
        m = re.search(r"(?i)retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", raw or "")
        secs = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(float(m.group(3) or 0))) if m and any(m.groups()) else None
    return time.time() + secs if secs else None


def _exhausted_file(name):
    return os.path.join(provider_dir(name), "exhausted.json")


def exhausted_until(name, model, accounts=None):
    """Owner 21.11 18:45: a model whose daily quota the provider said is used up is skipped only until the time the provider gave
    (never removed). With several accounts it is skipped only when every account's quota is out. 0 = usable."""
    try:
        d = json.load(open(_exhausted_file(name), encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    n = accounts or max(1, len([k for k in d if k.startswith(f"{model}#")]) or 1)
    times = [float(d.get(f"{model}#{i}") or d.get(model if i == 0 else "") or 0) for i in range(n)]
    if not times or min(times) <= time.time():
        return 0
    return min(times)


def _note_exhausted(name, model, until):
    try:
        d = json.load(open(_exhausted_file(name), encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = {k: v for k, v in d.items() if float(v or 0) > time.time()}
    d[model] = until
    os.makedirs(os.path.dirname(_exhausted_file(name)), exist_ok=True)
    with open(_exhausted_file(name), "w", encoding="utf-8") as f:
        json.dump(d, f)


def _post(name, p, key, body, timeout):
    """One request; one more only when the provider itself says when (retry-after / retryDelay up to 60 s). `key` may be a list of the
    provider's account keys: when one account's key is refused or its daily quota is out, the next account gets the same request."""
    keys = key if isinstance(key, (list, tuple)) else [key]
    last = None
    for i, k in enumerate(keys):
        try:
            return _post_one(name, p, k, body, timeout, i)
        except DirectError as e:
            last = e
            if i + 1 < len(keys) and _key_moves_on(e.code, e.raw):
                _append(os.path.join(provider_dir(name), "requests.log"), {"at": _now(), "model": body.get("model"),
                        "next_account": i + 2, "why": e.code})
                continue
            raise
    raise last or DirectError(0, "no key")


def _post_one(name, p, key, body, timeout, key_index=0):
    url = _base(p) + "/chat/completions"
    hdr = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        hdr["Authorization"] = "Bearer " + key
    for attempt in (1, 2):
        t0 = time.time()
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr), timeout=timeout)
            out = json.loads(r.read().decode("utf-8", "replace"))
            u = out.get("usage") or {}
            _append(os.path.join(provider_dir(name), "requests.log"), {"at": _now(), "model": body.get("model"), "status": 200,
                    "seconds": round(time.time() - t0, 1), "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
                    "limits": {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}})
            return out
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            _append(os.path.join(provider_dir(name), "requests.log"), {"at": _now(), "model": body.get("model"), "status": e.code,
                    "seconds": round(time.time() - t0, 1), "error": " ".join(raw.split())[:500]})
            wait = _retry_seconds(dict(e.headers.items()), raw) if e.code in (429, 503) else None
            if e.code == 429:
                until = _daily_until(raw)
                if until:
                    _note_exhausted(name, f"{body.get('model')}#{key_index}", until)
                    raise DirectError(e.code, raw)
            if e.code == 503 and wait is None:
                wait = BUSY_RETRY_SEC
            if attempt == 1 and wait is not None and wait <= 60:
                time.sleep(max(1, wait))
                continue
            raise DirectError(e.code, raw)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            _append(os.path.join(provider_dir(name), "requests.log"), {"at": _now(), "model": body.get("model"), "status": 0, "error": str(e)[:300]})
            raise DirectError(0, f"unreachable: {e}")
    raise DirectError(0, "no answer")


def probe(name, p, key, model, text, max_tokens=200, timeout=90):
    """One plain request straight to the provider (the switch checks): (answered, raw error text). Owner 21.11: these went through the
    translator in Anthropic's format even after the conversations moved to the bridge itself."""
    field = (p.get("upstream") or {}).get("max_tokens_field") or "max_tokens"
    try:
        out = _post(name, p, key, {"model": model, "messages": [{"role": "user", "content": text}], field: max_tokens}, timeout)
    except DirectError as e:
        return False, f"HTTP {e.code} " + e.raw[:300] if e.code else e.raw[:300]
    m = ((out.get("choices") or [{}])[0].get("message")) or {}
    ok = bool((m.get("content") or m.get("reasoning_content") or m.get("reasoning") or "").strip())
    return ok, "" if ok else "the answer had no content"


SUMMARY_TEMPLATE = (
    "כתוב תקציר זיכרון של השיחה שלמטה, בעברית, בלי אייקונים ובלי עיצוב, עד 25 שורות, כדי שבינה אחרת תמשיך ממנו. אם למעלה יש סיכום קודם — "
    "מזג אותו (הזיכרון מצטבר, לא מתחיל מחדש). מלא רק סעיפים שיש להם תוכן, בסדר הזה: "
    "1) כותרת: התאריך וטווח השעות שהתקציר מכסה. 2) נושא: שורה אחת. 3) מה יקיר רוצה. 4) מה יקיר צריך. "
    "5) נקודות שיקיר דיבר עליהן, בקצרה. 6) החלטות של יקיר, כל אחת עם השעה. "
    "7) מה בוצע: קבצים, פקודות, שינויים — בשם מדויק. 8) פתוח והצעד הבא. 9) שאלות שהבינה שאלה ויקיר עוד לא ענה. "
    "10) התחייבויות של הבינה שעוד לא בוצעו. 11) תקלות שקרו ומה מצבן. 12) מה ממתין לאישור של יקיר (טיוטות, פעולות). "
    "13) העדפות ותיקונים שיקיר נתן. 14) עובדות מדויקות — שמות, מספרים, נתיבים, מזהים — בציטוט, לא בפרפרזה. 15) מה אסור לעשות. "
    "ליד כל טענה כתוב (נבדק) או (לא נבדק). רק התקציר, בלי הקדמה.")


def summarize(name, p, key, model, text, timeout=120):
    """A short summary of the conversation by the provider itself (no tools): the memory a provider without its own session keeps."""
    field = (p.get("upstream") or {}).get("max_tokens_field") or "max_tokens"
    body = {"model": model, field: 1600, "messages": [
        {"role": "system", "content": SUMMARY_TEMPLATE},
        {"role": "user", "content": text}]}
    out = _post(name, p, key, body, timeout)
    return (((out.get("choices") or [{}])[0].get("message")) or {}).get("content", "").strip()


def run(name, p, key, model, system_text, user_text, on_tool=None, timeout=600, mcp=None):
    """The whole exchange with one provider: the request, every tool round, the final answer. {"text", "in", "out", "tools"}."""
    always, rule_items = split_rules(system_text)
    sent_rules = always + (("\n\nתוכן עניינים של כללי הגשר (bridge_rules ומספר לסעיף המלא):\n" + rules_contents(rule_items)) if rule_items else "")
    msgs = [{"role": "system", "content": connection_text(name) + "\n\n" + sent_rules}, {"role": "user", "content": user_text}]
    field = (p.get("upstream") or {}).get("max_tokens_field") or "max_tokens"
    tin = tout = 0
    used, seen = [], {}
    end = time.time() + (timeout or 600)
    _append(os.path.join(provider_dir(name), "requests.log"), {"at": _now(), "parts_chars": {
        "connection": len(connection_text(name)), "rules_sent": len(sent_rules), "rules_full": len(system_text or ""),
        "conversation_and_message": len(user_text or ""),
        "tools": len(json.dumps(META_TOOLS, ensure_ascii=False))}})
    for _ in range(MAX_ROUNDS):
        # the answer's room: Groq counts it against its 8000 tokens a minute, so it is kept modest (providers.json "max_tokens" overrides)
        body = {"model": model, "messages": msgs, "tools": META_TOOLS, field: int(p.get("max_tokens") or 2048)}
        out = _post(name, p, key, body, timeout=max(30, min(300, end - time.time())))
        u = out.get("usage") or {}
        tin, tout = tin + (u.get("prompt_tokens") or 0), tout + (u.get("completion_tokens") or 0)
        m = ((out.get("choices") or [{}])[0].get("message")) or {}
        calls = m.get("tool_calls") or []
        if not calls:
            return {"text": (m.get("content") or "").strip(), "in": tin, "out": tout, "tools": used}
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = (c.get("function") or {}).get("name", "")
            try:
                a = json.loads((c.get("function") or {}).get("arguments") or "{}") or {}
            except ValueError:
                a = {}
            sig = fn + json.dumps(a, sort_keys=True, ensure_ascii=False)
            t0 = time.time()
            if sig in seen:  # the same call twice: the first result again, no second run
                res, ok = seen[sig], True
            else:
                try:
                    if fn == "bridge_rules":
                        k = a.get("number")
                        res = (rule_items[int(k) - 1][1] if str(k).isdigit() and 0 < int(k) <= len(rule_items)
                               else rules_contents(rule_items) or "אין כללים נוספים")
                    elif fn == "bridge_tools":
                        res = definition(name, a["name"], mcp) if a.get("name") else contents(name, mcp)
                    elif fn == "bridge_call":
                        tool = a.get("name", "")
                        if on_tool:
                            on_tool(tool, a.get("args") or {})
                        res = call_tool(name, tool, a.get("args") or {}, mcp)
                    elif fn == "save_tool":
                        res = save_tool(name, a.get("name"), a.get("description"), a.get("code"), a.get("parameters"), bool(a.get("shared")))
                        write_index_safe()
                    else:
                        res = f"אין כלי בשם {fn}: השתמש ב-bridge_tools / bridge_call / save_tool"
                    ok = not str(res).startswith(("שגיאה", "אין כלי"))
                except Exception as e:
                    res, ok = f"שגיאה בכלי: {type(e).__name__}: {str(e)[:300]}", False
                seen[sig] = res
            if fn == "bridge_call":
                used.append(a.get("name", ""))
                _append(os.path.join(provider_dir(name), "tools.log"), {"at": _now(), "tool": a.get("name", ""), "ok": ok, "seconds": round(time.time() - t0, 1)})
            msgs.append({"role": "tool", "tool_call_id": c.get("id") or fn, "content": str(res)[:OUT_CAP]})
        if time.time() > end:
            break
    raise DirectError(0, f"no final answer after {MAX_ROUNDS} tool rounds")


_INDEX_NAMES = []


def write_index_safe():
    if _INDEX_NAMES:
        write_index(_INDEX_NAMES)


def remember_providers(names):
    _INDEX_NAMES[:] = list(names)
