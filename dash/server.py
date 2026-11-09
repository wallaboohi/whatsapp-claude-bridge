"""Dashboard backend of the WhatsApp ↔ Claude tool.

Standalone: reads the tool's local databases directly (read-only) and talks to the bridge when it is up, so the
dashboard works without the agent, without Claude and without internet. Served by the desktop app (WhatsAppDash.exe)
and by the agent on http://127.0.0.1:8770. Bound to localhost only.
"""
import datetime as dt
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PKG = getattr(sys, "_MEIPASS", None) and os.path.join(sys._MEIPASS, "dash") or os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(PKG, "static")
VERSION = open(os.path.join(PKG, "VERSION")).read().strip()
SETTINGS = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WhatsAppDash", "settings.json")


def _root():
    """Where the tool is installed: env WA_TOOL_ROOT, the app's settings.json, or the source checkout."""
    if os.environ.get("WA_TOOL_ROOT"):
        return os.environ["WA_TOOL_ROOT"]
    try:
        return json.load(open(SETTINGS, encoding="utf-8-sig"))["tool_root"]  # PowerShell 5 writes a BOM
    except Exception:
        return os.path.dirname(PKG)


ROOT = _root()


def cfg():
    return json.load(open(os.path.join(ROOT, "agent", "config.json"), encoding="utf-8"))


def paths():
    c = cfg()
    store = os.path.expanduser(c["store_dir"])
    store = store if os.path.isabs(store) else os.path.join(ROOT, store)
    return {"msg": os.path.join(store, "messages.db"), "wa": os.path.join(store, "whatsapp.db"),
            "agent": os.path.join(ROOT, "agent", "agent.db"), "log": os.path.join(ROOT, "agent", "agent.log"),
            "token": os.path.join(store, "api_token"), "owner_token": os.path.join(store, "owner_token"),
            "policy": os.path.join(store, "send_policy.json"),
            "bridge_log": os.path.join(ROOT, "whatsapp-bridge", "bridge.log")}


def bridge(path, body=None, timeout=8):
    c, p = cfg(), paths()
    tok = ""
    for k in ("owner_token", "token"):  # owner token: approvals / policy / direct actions; api token = older bridges
        try:
            tok = open(p[k]).read().strip()
            break
        except OSError:
            pass
    req = urllib.request.Request(c["bridge_url"] + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def rows(db, sql, args=()):
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


LOCAL = "local"


def primary():
    return cfg()["claude_group"]


def _groups_file():
    return os.path.join(ROOT, "agent", "groups.json")


def dump_json(obj, path, **kw):
    """Write JSON atomically (temp file + replace) so a reader never sees a half-written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, **kw)
    os.replace(tmp, path)


def load_groups_raw():
    try:
        return json.load(open(_groups_file(), encoding="utf-8"))
    except Exception:
        return {}


def save_groups_raw(raw):
    dump_json(raw, _groups_file(), ensure_ascii=False, indent=2)


def conversations():
    """Ordered jid -> info for every conversation: primary control group, connected groups, the local chat."""
    raw, prim = load_groups_raw(), primary()
    out = {prim: {"name": "4M", "purpose": "4m", **(raw.get(prim) or {})}}
    for jid, v in raw.items():
        if jid not in out and jid != LOCAL and v.get("enabled", True):
            out[jid] = dict(v)
    out[LOCAL] = {"name": "שיחה מקומית", "purpose": "local", **(raw.get(LOCAL) or {})}
    try:  # one source for names (owner request 09.11): the name WhatsApp shows now, as the bridge keeps it in chats
        live = {r["jid"]: r["name"] for r in rows(paths()["msg"], "SELECT jid, name FROM chats WHERE jid LIKE '%@g.us'") if (r["name"] or "").strip()}
    except Exception:
        live = {}
    for jid, v in out.items():
        if jid != LOCAL and live.get(jid):
            v["name"] = live[jid]
    return out


def K(name, chat=None):
    return name if not chat or chat == primary() else f"{name}:{chat}"


def rules_rel(chat, g):
    """The conversation's base-rules file under agent/home (same rule as agent.rules_rel)."""
    if g.get("instructions"):
        return g["instructions"]
    if chat == primary():
        return "groups/main.md"
    return f"groups/{g.get('slug') or re.sub(r'[^0-9a-zA-Z]+', '', chat.split('@')[0])[:12] or 'chat'}.md"


def rules_of(chat):
    rel = rules_rel(chat, conversations().get(chat) or {})
    try:
        text = open(os.path.join(ROOT, "agent", "home", rel), encoding="utf-8").read()
    except OSError:
        text = ""
    return {"path": "agent/home/" + rel, "text": text}


def sync_policy_groups():
    """Tell the bridge which extra groups are Claude conversations (their replies are sent directly, not as drafts)."""
    jids = [j for j in conversations() if j not in (primary(), LOCAL)]
    res = bridge("/policy", {"control_groups": jids})
    if "error" in res:  # bridge down: it reads the policy file on every send
        pol = local_policy()
        pol["control_groups"] = jids
        dump_json(pol, paths()["policy"], ensure_ascii=False, indent=2)


def agent_db_rw():
    """Writable connection to agent.db (queue a PC message, local chat history) with the columns the agent adds."""
    con = sqlite3.connect(paths()["agent"], timeout=30)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT, text TEXT, at TEXT,
            status TEXT DEFAULT 'pending', answered TEXT);
        CREATE TABLE IF NOT EXISTS local_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT, at TEXT, role TEXT, text TEXT, files TEXT);
    """)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'claude_runs'").fetchone():
        if "chat" not in [r[1] for r in con.execute("PRAGMA table_info(claude_runs)")]:
            con.execute("ALTER TABLE claude_runs ADD COLUMN chat TEXT")
    have = [r[1] for r in con.execute("PRAGMA table_info(commands)")]
    con.executescript("""
        CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT DEFAULT 'time', text TEXT,
            due TEXT, chat_jid TEXT, ref_ts TEXT, status TEXT DEFAULT 'active', created TEXT, fired TEXT);
        CREATE TABLE IF NOT EXISTS scheduled_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER, send_at TEXT,
            status TEXT DEFAULT 'scheduled', chat TEXT, created TEXT, done TEXT);
    """)
    for col, ddl in (("chat_jid", "TEXT"), ("source", "TEXT DEFAULT 'wa'"), ("sync", "INTEGER DEFAULT 0"), ("attempts", "INTEGER DEFAULT 0"),
                     ("retry_at", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE commands ADD COLUMN {col} {ddl}")
    return con


def kv(k):
    r = rows(paths()["agent"], "SELECT v FROM kv WHERE k = ?", (k,))
    return r[0]["v"] if r else None


def kv_put(k, v):
    con = agent_db_rw()
    try:
        con.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, v))
        con.commit()
    finally:
        con.close()


def save_cfg(c):
    dump_json(c, os.path.join(ROOT, "agent", "config.json"), ensure_ascii=False, indent=2)


NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TASKS = {"bridge": "WhatsApp bridge", "agent": "WhatsApp agent"}
_svc_cache = {"at": 0, "v": None}


def _ps(cmd, timeout=40):
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=timeout, creationflags=NW)


def services(force=False):
    """Is each run-*.cmd loop / process alive (cached 15 s: the dashboard polls every 10 s)."""
    if not force and _svc_cache["v"] is not None and time.time() - _svc_cache["at"] < 15:
        return _svc_cache["v"]
    out = {"bridge_loop": None, "agent_loop": None, "bridge_proc": None, "agent_proc": None}
    try:
        r = _ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run-bridge.cmd*' -or $_.CommandLine -like '*run-agent.cmd*' "
                "-or $_.Name -eq 'whatsapp-bridge.exe' -or $_.CommandLine -like '*agent.py*' } | ForEach-Object { $_.Name + '|' + $_.CommandLine }", 20)
        lines = r.stdout.splitlines()
        out = {"bridge_loop": any("run-bridge.cmd" in l and l.lower().startswith("cmd") for l in lines),
               "agent_loop": any("run-agent.cmd" in l and l.lower().startswith("cmd") for l in lines),
               "bridge_proc": any(l.lower().startswith("whatsapp-bridge.exe") for l in lines),
               "agent_proc": any("agent.py" in l and "python" in l.lower() for l in lines)}
    except Exception:
        pass
    _svc_cache.update(at=time.time(), v=out)
    return out


def svc_stop(name):
    """Really stop the bridge / agent: end its Task Scheduler task, its run-*.cmd loop, then the process."""
    subprocess.run(["schtasks", "/end", "/tn", TASKS[name]], capture_output=True, timeout=20, creationflags=NW)
    _ps(f"Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object {{ $_.CommandLine -like '*run-{name}.cmd*' }} | "
        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    if name == "bridge":
        _ps("Get-Process whatsapp-bridge -ErrorAction SilentlyContinue | Stop-Process -Force")
    else:
        _ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*agent.py*' -and $_.CommandLine -notlike '*update.py*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    _svc_cache["v"] = None


def svc_start(name):
    """Start the run-*.cmd loop: through its Task Scheduler task when it exists, otherwise a hidden console."""
    r = subprocess.run(["schtasks", "/run", "/tn", TASKS[name]], capture_output=True, timeout=20, creationflags=NW)
    if r.returncode != 0:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        for f in (flags | 0x01000000, flags):  # break away from our job when allowed, so it outlives this process
            try:
                subprocess.Popen(["conhost.exe", "--headless", "cmd.exe", "/c", os.path.join(ROOT, f"run-{name}.cmd")], cwd=ROOT,
                                 creationflags=f, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
                break
            except OSError:
                continue
    _svc_cache["v"] = None


HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PERM_MODES = ["default", "acceptEdits", "auto", "bypassPermissions", "plan"]
VOICES = ["pt-BR-ThalitaMultilingualNeural", "en-US-AvaMultilingualNeural", "en-US-EmmaMultilingualNeural",
          "fr-FR-VivienneMultilingualNeural", "de-DE-SeraphinaMultilingualNeural", "he-IL-HilaNeural", "he-IL-AvriNeural",
          "en-US-AndrewMultilingualNeural"]
VOICE_STYLES = ["normal", "soft"]
# every setting the agent reads from config.json that the owner may want to change: key -> (type, default, extra)
SETTINGS = {
    "backup_time": ("time", "03:00", None), "update_check_time": ("time", "09:00", None), "group_check_time": ("time", "10:00", None),
    "selftest_time": ("time", "04:30", None), "morning_summary_time": ("time", "07:30", None), "signin_check_time": ("time", "09:30", None),
    "auto_update_time": ("time", "23:50", None), "auto_release_time": ("time", "23:30", None),
    "auto_update": ("bool", True, None), "auto_release": ("bool", False, None), "morning_summary": ("bool", True, None), "voice_replies": ("bool", True, None),
    "transcribe": ("bool", True, None), "transcribe_all_chats": ("bool", True, None), "fresh_sessions": ("bool", False, None),
    "reply_italic": ("bool", True, None), "quote_replies": ("bool", True, None), "strict_mcp": ("bool", True, None),
    "daily_budget_usd": ("num", 20, (0, 1000)), "total_daily_budget_usd": ("num", None, (0, 5000)),
    "session_max_runs": ("int", 60, (1, 1000)), "claude_timeout_minutes": ("int", 15, (1, 240)), "disconnect_alert_minutes": ("int", 10, (1, 1440)),
    "backup_keep_days": ("int", 14, (1, 365)), "backup_media_days": ("int", 90, (0, 3650)), "reconnect_settle_seconds": ("int", 45, (0, 600)),
    "light_model": ("model", "haiku", None), "claude_model": ("model", "sonnet", None), "deep_model": ("model", "opus", None),
    "permission_mode": ("choice", "auto", PERM_MODES), "voice_name": ("voice", "pt-BR-ThalitaMultilingualNeural", VOICES), "voice_style": ("vstyle", "normal", VOICE_STYLES), "group_name_model": ("bool", True, None),
    "peer_watch": ("bool", True, None), "peer_alert_minutes": ("int", 10, (1, 1440)), "peer_offline_minutes": ("int", 30, (1, 10080)),
    "peer_backup": ("bool", True, None), "peer_backup_max_mb": ("int", 2048, (0, 200000)),
    "host_label": ("opt", "", None), "claude_account": ("opt", "", None),
    "language": ("choice", "he", ["he", "en"]),
}


def settings_view():
    c = cfg()
    try:
        models = [k for k in json.load(open(os.path.join(ROOT, "agent", "models.json"), encoding="utf-8")) if k != "auto"]
    except Exception:
        models = []
    models = list(dict.fromkeys(models + ["haiku", "sonnet", "opus"]))
    vals = {k: c.get(k, d) for k, (t, d, x) in SETTINGS.items()}
    if vals.get("total_daily_budget_usd") is None:
        vals["total_daily_budget_usd"] = 2 * float(c.get("daily_budget_usd", 20))
    return {"values": vals, "types": {k: t for k, (t, d, x) in SETTINGS.items()}, "models": models, "perm_modes": PERM_MODES, "voices": VOICES,
            "choices": {k: x for k, (t, d, x) in SETTINGS.items() if t == "choice"},
            "plugins": [{"path": x, "name": os.path.basename(x), "on": True} for x in c.get("plugins", [])] +
                       [{"path": x, "name": os.path.basename(x), "on": False} for x in c.get("plugins_disabled", [])],
            "peers": c.get("peers", [])}


def validate_setting(k, v):
    t, d, x = SETTINGS[k]
    if t == "time":
        if not HHMM.match(str(v)):
            raise ValueError(f"{k}: שעה בפורמט HH:MM")
        return str(v)
    if t == "bool":
        return bool(v)
    if t in ("num", "int"):
        try:
            n = float(v) if t == "num" else int(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"{k}: מספר")
        if not x[0] <= n <= x[1]:
            raise ValueError(f"{k}: בין {x[0]} ל-{x[1]}")
        return n
    if t in ("choice", "voice", "vstyle"):
        if v not in x:
            raise ValueError(f"{k}: אחד מ-{', '.join(x)}")
        return v
    if t == "model":
        if not re.fullmatch(r"[a-z0-9.\-\[\]]{2,40}", str(v)):
            raise ValueError(f"{k}: שם מודל")
        return str(v)
    v = str(v).strip()
    if t == "opt" and v == "":
        return ""
    if not 0 < len(v) <= 80:
        raise ValueError(f"{k}: טקסט")
    return v


def peers():
    return cfg().get("peers") or []


def peer_alias(pr):
    return str(pr.get("ssh") or pr.get("tunnel") or "").strip()


def backup_dir_abs():
    v = os.path.expanduser(str(cfg().get("backup_dir", "~/WhatsApp-Backup")))
    return os.path.normpath(v if os.path.isabs(v) else os.path.join(ROOT, v))


def cost_summary():
    """Claude spend on this machine: today / 7 / 30 days, with the machine name and the Claude account."""
    c, p = cfg(), paths()
    d0 = dt.date.today()
    one = lambda days: round((rows(p["agent"], "SELECT coalesce(sum(cost),0) s FROM claude_runs WHERE substr(at,1,10) >= ?",
                                  ((d0 - dt.timedelta(days=days - 1)).isoformat(),)) or [{"s": 0}])[0]["s"], 2)
    return {"machine": c.get("host_label") or socket.gethostname(), "account": c.get("claude_account") or "",
            "today": one(1), "d7": one(7), "d30": one(30)}


def cost_by_chat():
    c, p = cfg(), paths()
    since = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    convs = conversations()
    out = []
    for r in rows(p["agent"], "SELECT coalesce(chat, ?) AS chat, round(sum(CASE WHEN substr(at,1,10) = ? THEN cost ELSE 0 END),2) AS today, "
                              "round(sum(cost),2) AS d7, count(*) AS runs FROM claude_runs WHERE substr(at,1,10) >= ? GROUP BY 1 ORDER BY 3 DESC",
                  (c["claude_group"], dt.date.today().isoformat(), since)):
        out.append(dict(r, name=(convs.get(r["chat"]) or {}).get("name") or ("שיחה מקומית" if r["chat"] == LOCAL else str(r["chat"]).split("@")[0])))
    return out


def transcribe_gap_info():
    """Voice notes with no transcript since transcription was switched off (same rule as the agent's transcribe_gap)."""
    c, p = cfg(), paths()
    since = kv("transcribe_off_since") or (dt.datetime.now() - dt.timedelta(days=7)).isoformat(timespec="seconds")
    s19 = since[:19].replace("T", " ")
    convs = [c["claude_group"]] + [j for j in load_groups_raw() if j != LOCAL]
    if c.get("transcribe_all_chats", True):
        aud = rows(p["msg"], "SELECT id, chat_jid FROM messages WHERE media_type = 'audio' AND timestamp >= ? LIMIT 5000", (s19,))
    else:
        aud = rows(p["msg"], "SELECT id, chat_jid FROM messages WHERE media_type = 'audio' AND is_from_me = 1 AND chat_jid IN (%s) "
                             "AND timestamp >= ? LIMIT 5000" % ",".join("?" * len(convs)), (*convs, s19))
    done = {(r["id"], r["chat_jid"]) for r in rows(p["agent"], "SELECT id, chat_jid FROM transcripts")}
    try:
        prog = json.loads(kv("transcribe_backfill") or "null")
    except ValueError:
        prog = None
    return {"on": bool(c.get("transcribe", True)), "since": since, "explicit": bool(kv("transcribe_off_since")),
            "missing": sum(1 for r in aud if (r["id"], r["chat_jid"]) not in done), "progress": prog,
            "requested": bool(kv("transcribe_backfill_request"))}


def peer_backups():
    out = {}
    for pr in peers():
        nm = pr.get("name") or peer_alias(pr)
        try:
            out[nm] = json.loads(kv(f"peer_backup:{nm}") or "null")
        except ValueError:
            out[nm] = None
    return out


def ssh_start_script(root):
    """PowerShell for the other machine: start its agent loop (which also hosts its dashboard) unless one already runs.
    Win32_Process.Create puts it outside the ssh session, so it keeps running after ssh disconnects."""
    cmd = os.path.join(root, "run-agent.cmd")
    return ("if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run-agent.cmd*' }) { 'already running' } else { "
            f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='conhost.exe --headless cmd.exe /c \"{cmd}\"'}} "
            "| Out-Null; 'started' }")


def peer_ssh_start(i):
    import base64
    ps = peers()
    if not (0 <= i < len(ps)) or not peer_alias(ps[i]):
        return {"error": "no ssh connection configured for this computer"}
    enc = base64.b64encode(ssh_start_script(ps[i].get("root") or ROOT).encode("utf-16-le")).decode()
    ssh = shutil.which("ssh") or r"C:\Windows\System32\OpenSSH\ssh.exe"
    try:
        r = subprocess.run([ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", peer_alias(ps[i]), f"powershell -NoProfile -EncodedCommand {enc}"],
                           capture_output=True, text=True, timeout=60, creationflags=NW)
    except Exception as e:
        return {"error": str(e)[:200]}
    out = (r.stdout or "").strip().splitlines()
    return {"ok": r.returncode == 0, "result": out[-1] if out else (r.stderr or "")[-200:]}


def peer_url(i):
    ps = peers()
    if not (0 <= i < len(ps)) or not str(ps[i].get("url", "")).startswith("http://127.0.0.1:"):
        raise ValueError("unknown peer")
    return ps[i]["url"].rstrip("/")


def peer_call(i, path, body=None, raw=None, ctype="application/json", timeout=5):
    """Forward a request to the other machine's dashboard (through the ssh tunnel). Unreachable -> {"offline": true}."""
    try:
        url = peer_url(i) + path
    except ValueError as e:
        return {"offline": True, "error": str(e)}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode() or "{}")
    except Exception as e:
        return {"offline": True, "error": str(e)[:200]}


def git_state():
    """This machine's code for the other machine's nightly release check: HEAD and how many tracked files are unsaved."""
    try:
        run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=20, creationflags=NW)
        head = run("rev-parse", "HEAD").stdout.strip()
        dirty = len([ln for ln in run("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if ln.strip()])
        return {"head": head, "dirty": dirty} if head else None
    except Exception:
        return None


def brief():
    """Small status of this machine for the other machine's dashboard (tabs + the "computers" card)."""
    c, p = cfg(), paths()
    b = bridge("/status", timeout=3)
    hb = kv("heartbeat")
    alive = bool(hb) and (dt.datetime.now() - dt.datetime.fromisoformat(hb)).total_seconds() < 180
    gl = []
    for jid, g in conversations().items():
        lv = kv(K("live_steps", jid))
        try:
            lvj = json.loads(lv) if lv else None
        except ValueError:
            lvj = None
        gl.append({"jid": jid, "name": g.get("name") or jid, "purpose": g.get("purpose", ""), "live": bool(lvj and not lvj.get("done")),
                   "paused": bool(kv(K("listen_paused", jid))),
                   "pending": (rows(p["agent"], "SELECT count(*) n FROM commands WHERE status IN ('pending','running') AND coalesce(chat_jid, ?) = ?",
                                    (c["claude_group"], jid)) or [{"n": 0}])[0]["n"]})
    tunnels = {}
    for pr in c.get("peers") or []:
        nm = pr.get("name") or pr.get("tunnel") or ""
        try:
            tunnels[nm] = json.loads(kv(f"tunnel:{nm}") or "null")
        except ValueError:
            tunnels[nm] = None
    try:  # which WhatsApp account this PC's bridge runs (PCs may share one): the peer decides who watches people waiting
        acc = (rows(p["wa"], "SELECT jid FROM whatsmeow_device LIMIT 1") or [{"jid": ""}])[0]["jid"].split("@")[0].split(":")[0]
    except Exception:
        acc = ""
    return {"machine": socket.gethostname(), "wa_account": acc, "tool_version": (open(os.path.join(ROOT, "VERSION")).read().strip() if os.path.exists(os.path.join(ROOT, "VERSION")) else "?"),
            "dash_version": VERSION, "bridge": {k: b.get(k) for k in ("connected", "version", "paused", "error", "connected_since", "health")},
            "agent_alive": alive, "heartbeat": hb, "listen_paused": kv("listen_paused") or "", "groups": gl, "tunnels": tunnels,
            "services": services(), "backup_dir": backup_dir_abs(), "cost": cost_summary(),
            "host_label": c.get("host_label") or "", "account": c.get("claude_account") or "", "git": git_state()}


def peers_status():
    """This machine + every configured peer (fetched in parallel, short timeout)."""
    ps = peers()
    out = [None] * len(ps)

    def one(i):
        r = peer_call(i, "/brief", timeout=4)
        out[i] = {"i": i, "name": ps[i].get("name") or f"peer {i}", "tunnel": ps[i].get("tunnel") or "", "ssh": peer_alias(ps[i]),
                  "online": not r.get("offline"),
                  "brief": None if r.get("offline") else r, "error": r.get("error")}
    th = [threading.Thread(target=one, args=(i,)) for i in range(len(ps))]
    [t.start() for t in th]
    [t.join(6) for t in th]
    return {"self": brief(), "peers": [x for x in out if x]}


def names():
    p = paths()
    nm = {}
    for r in rows(p["wa"], "SELECT their_jid, full_name, push_name FROM whatsmeow_contacts"):
        nm[r["their_jid"].split("@")[0]] = r["full_name"] or r["push_name"]
    for r in rows(p["wa"], "SELECT lid, pn FROM whatsmeow_lid_map"):
        if r["pn"] in nm:
            nm[r["lid"]] = nm[r["pn"]]
    for r in rows(p["msg"], "SELECT jid, name FROM chats WHERE jid LIKE '%@g.us'"):
        nm[r["jid"].split("@")[0]] = r["name"]
    return nm


def local_policy():
    try:
        return json.load(open(paths()["policy"], encoding="utf-8"))
    except Exception:
        return {"mode": "draft"}


def conversation_of(chat, c, p):
    """The chat window of one tab: WhatsApp messages of the group + messages typed/answered only in the dashboard, by time."""
    msgs = []
    if chat != LOCAL:
        msgs = rows(p["msg"], "SELECT id, timestamp, is_from_me, content, media_type FROM messages WHERE chat_jid = ? "
                              "ORDER BY rowid DESC LIMIT 60", (chat,))[::-1]
        for m in msgs:
            m["tool"] = (m["content"] or "").startswith(c["reply_prefix"].strip())
            if m["tool"]:
                m["content"] = m["content"][len(c["reply_prefix"].strip()):].strip()
    loc = rows(p["agent"], "SELECT id, at, role, text, files FROM local_messages WHERE chat = ? ORDER BY id DESC LIMIT 60", (chat,))[::-1]
    for r in loc:
        try:
            files = json.loads(r["files"] or "[]")
        except ValueError:
            files = []
        msgs.append({"id": f"L{r['id']}", "timestamp": str(r["at"]).replace("T", " "), "is_from_me": 1, "content": r["text"],
                     "media_type": "", "tool": r["role"] == "tool", "src": "pc", "files": files})
    msgs.sort(key=lambda m: str(m["timestamp"])[:19])
    return msgs[-80:]


_schema_ok = False


def data(chat=None):
    global _schema_ok
    if not _schema_ok:
        try:
            agent_db_rw().close()
            _schema_ok = True
        except Exception:
            pass
    c, p = cfg(), paths()
    nm = names()
    label = lambda j: nm.get(str(j).split("@")[0]) or str(j).split("@")[0]
    convs = conversations()
    group = chat if chat in convs else c["claude_group"]
    day0 = (dt.datetime.now() - dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    today = dt.date.today().isoformat()
    b = bridge("/status")
    jl = lambda k: json.loads(kv(k)) if kv(k) else None
    convo = conversation_of(group, c, p)
    today0 = dt.date.today().isoformat()
    glist = []
    for jid, g in convs.items():
        sp = rows(p["agent"], "SELECT coalesce(sum(cost),0) s FROM claude_runs WHERE substr(at,1,10) = ? AND (chat = ?" +
                  (" OR chat IS NULL)" if jid == c["claude_group"] else ")"), (today0, jid))
        glist.append({"jid": jid, "name": (nm.get(jid.split("@")[0]) if jid != LOCAL else None) or g.get("name") or jid, "purpose": g.get("purpose", ""),
                      "paused": kv(K("listen_paused", jid)) or "", "session": (kv(K("session_id", jid)) or "")[:8],
                      "session_started": kv(K("session_started", jid)), "session_runs": kv(K("session_runs", jid)) or "0",
                      "budget": float(g.get("budget_usd") or c.get("daily_budget_usd", 5)), "spent": round(sp[0]["s"], 2) if sp else 0,
                      "model": model_info(jid)["current"], "primary": jid == c["claude_group"],
                      "live": (json.loads(kv(K("live_steps", jid))) if kv(K("live_steps", jid)) else None),
                      "pending": (rows(p["agent"], "SELECT count(*) n FROM commands WHERE status IN ('pending','running') AND coalesce(chat_jid, ?) = ?", (c["claude_group"], jid)) or [{"n": 0}])[0]["n"]})
    pc_last = (rows(p["agent"], "SELECT max(at) m FROM local_messages WHERE role = 'me'") or [{"m": None}])[0]["m"]
    phone_last = (rows(p["msg"], "SELECT max(timestamp) m FROM messages WHERE is_from_me = 1 AND chat_jid IN (%s) AND content NOT LIKE ?" %
                       ",".join("?" * len([j for j in convs if j != LOCAL])), [j for j in convs if j != LOCAL] + [c["reply_prefix"].strip() + "%"]) or [{"m": None}])[0]["m"]
    hourly = rows(p["msg"], "SELECT substr(timestamp,12,2) h, count(*) n FROM messages WHERE substr(timestamp,1,19) > ? GROUP BY 1", (day0,))
    spent = rows(p["agent"], "SELECT coalesce(sum(cost),0) s FROM claude_runs WHERE substr(at,1,10) = ?", (today,))
    return {
        "version": VERSION,
        "tool_version": (open(os.path.join(ROOT, "VERSION")).read().strip() if os.path.exists(os.path.join(ROOT, "VERSION")) else "?"),
        "maintainer": bool(c.get("maintainer")),
        "now": dt.datetime.now().isoformat(timespec="seconds"),
        "bridge": b,
        "policy": b.get("policy") if "policy" in b else local_policy(),
        "conn_log": rows(p["msg"], "SELECT at, event FROM (SELECT at, event FROM conn_log WHERE at <= ? ORDER BY at DESC LIMIT 1) "
                                   "UNION ALL SELECT at, event FROM conn_log WHERE at > ? ORDER BY at", (day0, day0)),
        "received": [dict(r, sender=label(r["sender"]), chat=nm.get(str(r["chat_jid"]).split("@")[0]) or r["chat_name"] or label(r["chat_jid"]), group=str(r["chat_jid"]).endswith("@g.us")) for r in
                     rows(p["msg"], "SELECT m.chat_jid, m.sender, m.timestamp, m.content, m.media_type, c.name AS chat_name FROM messages m "
                                    "LEFT JOIN chats c ON c.jid = m.chat_jid WHERE m.is_from_me = 0 "
                                    "AND substr(m.timestamp,1,10) = ? ORDER BY m.timestamp DESC LIMIT 300", (today,))],
        "runs_today": rows(p["agent"], "SELECT * FROM claude_runs WHERE substr(at,1,10) = ? ORDER BY at DESC LIMIT 60", (today,)),
        "hourly": hourly,
        "received_today": (rows(p["msg"], "SELECT count(*) n FROM messages WHERE is_from_me = 0 AND substr(timestamp,1,10) = ?", (today,)) or [{"n": 0}])[0]["n"],
        "agent": {"heartbeat": kv("heartbeat"), "session_started": kv("session_started"), "backup_done": kv("backup_done"),
                  "events_connected": kv("events_connected"), "plugins": [os.path.basename(x) for x in c.get("plugins", [])],
                  "pending": (rows(p["agent"], "SELECT count(*) n FROM commands WHERE status IN ('pending','running')") or [{"n": 0}])[0]["n"],
                  "budget": c.get("daily_budget_usd", 5), "spent_today": round(spent[0]["s"], 2) if spent else 0,
                  "heartbeat_age": None},
        "chat": group, "groups": glist, "rules": rules_of(group),
        "presence": {"pc_last": str(pc_last).replace("T", " ") if pc_last else None, "phone_last": str(phone_last)[:19] if phone_last else None},
        "model": model_info(group),
        "live": (json.loads(kv(K("live_steps", group))) if kv(K("live_steps", group)) else None),
        "conversation": convo,
        "last_owner": jl("last_owner_msg"), "last_tool": jl("last_tool_msg"),
        "drafts": rows(p["msg"], "SELECT id, name, recipient, message, created, status FROM drafts ORDER BY id DESC LIMIT 40"),
        "sent": [dict(r, recipient=label(r["recipient"])) for r in
                 rows(p["msg"], "SELECT s.recipient, s.ok, s.detail, s.timestamp, s.recipient = ? AS own, "
                      "(SELECT substr(m.content, 1, 400) FROM messages m WHERE m.id = s.message_id LIMIT 1) AS text "
                      "FROM send_log s ORDER BY s.id DESC LIMIT 60", (c.get("claude_group", ""),))],
        "transcripts": [dict(r, sender=label(r["sender"]), chat=label(r["chat_jid"])) for r in
                        rows(p["agent"], "SELECT chat_jid, sender, ts, text FROM transcripts ORDER BY ts DESC LIMIT 40")],
        "transcribed_today": (rows(p["agent"], "SELECT count(*) n FROM transcripts WHERE substr(ts,1,10) = ?", (today,)) or [{"n": 0}])[0]["n"],
        "costs": rows(p["agent"], "SELECT substr(at,1,10) day, round(sum(cost),2) cost, count(*) runs FROM claude_runs GROUP BY 1 ORDER BY 1 DESC LIMIT 14")[::-1],
        "log": open(p["log"], encoding="utf-8").read().splitlines()[-60:] if os.path.exists(p["log"]) else [],
        "machine": socket.gethostname(),
        "listen_paused": kv("listen_paused") or "",
        "read_all": {j: bool(kv(K("read_all", j))) for j in conversations()},
        "limit_until": kv("limit_until") or "", "signin_ends": kv("signin_ends") or "",
        "queue": rows(p["agent"], "SELECT id, substr(text, 1, 240) AS text, at, status, coalesce(chat_jid, ?) AS chat, source, retry_at, attempts "
                                  "FROM commands WHERE status IN ('pending','running','waiting_limit','interrupted') ORDER BY id DESC LIMIT 60",
                      (c["claude_group"],)),
        "scheduled": rows(p["agent"], "SELECT id, draft_id, send_at, status, chat FROM scheduled_drafts WHERE status = 'scheduled' ORDER BY send_at"),
        "reminders": rows(p["agent"], "SELECT id, kind, text, due, chat_jid, status FROM reminders WHERE status = 'active' ORDER BY due LIMIT 60"),
        "disabled_groups": [{"jid": j, "name": v.get("name") or j} for j, v in load_groups_raw().items() if j != LOCAL and not v.get("enabled", True)],
        "settings": settings_view(),
        "services": services(),
        "jobs": {k: kv(f"day:{k}") for k in ("backup", "update_check", "group_check", "selftest", "morning_summary", "signin_check", "auto_update")},
        "cost_self": cost_summary(), "cost_by_chat": cost_by_chat(), "transcribe_gap": transcribe_gap_info(), "peer_backups": peer_backups(),
        "selftest": (json.load(open(os.path.join(ROOT, "agent", "selftest.json"), encoding="utf-8")) if os.path.exists(os.path.join(ROOT, "agent", "selftest.json")) else []),
    }


def kill(name_like):
    """Stop a tool process; its run-*.cmd loop restarts it within seconds."""
    ps = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{name_like}*' -and $_.Name -ne 'cmd.exe' "
          f"-and $_.Name -ne 'conhost.exe' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def git_action(kind, a):
    """Maintainer only (config.json "maintainer": true): save to git and publish a release from the dashboard."""
    if not cfg().get("maintainer"):
        return {"error": "not enabled (set maintainer: true in agent/config.json)"}
    import re
    nw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    git = lambda *x: subprocess.run(["git", *x], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120, creationflags=nw)
    if kind == "git_status":
        ver = open(os.path.join(ROOT, "VERSION")).read().strip()
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ver)
        nxt = f"{m[1]}.{m[2]}.{int(m[3]) + 1}" if m else ver
        changed = [l for l in git("status", "--porcelain").stdout.splitlines() if l.strip()]
        return {"version": ver, "next": nxt, "branch": git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                "changed": len(changed), "files": [l[3:] for l in changed[:15]], "last": git("log", "-1", "--format=%s").stdout.strip()}
    notes = (a.get("notes") or "").strip()
    if kind == "git_commit":
        if not notes:
            return {"error": "write a short description of the change"}
        # only files already in git are committed: a new file (possibly personal: photos, Excel, outbox) never slips in
        git("add", "-u")
        new = [l[3:] for l in git("status", "--porcelain").stdout.splitlines() if l.startswith("??")]
        r = git("commit", "-m", notes)
        out = (r.stdout + r.stderr)[-400:]
        if new:
            out += "\n❓ קבצים חדשים שלא נשמרו — מה לעשות איתם (לשמור / להתעלם)? " + ", ".join(new[:10])
        return {"ok": r.returncode == 0, "out": out, "untracked": new}
    ver = a.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        return {"error": "version must look like 1.0.2"}
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        return {"error": "there are unsaved changes - press 'save to git' first"}
    import tempfile
    nf = os.path.join(tempfile.gettempdir(), "wa_release_notes.txt")
    open(nf, "w", encoding="utf-8").write(notes)
    # visible window: the user watches the checks, the push and any error
    subprocess.Popen(["cmd.exe", "/c", "start", "Release", "cmd", "/k", os.path.join(ROOT, "release.cmd"), ver, f"--notes-file={nf}"], cwd=ROOT)
    return {"ok": True}


def _model_file():
    return os.path.join(ROOT, "agent", "model_override.json")


def model_info(chat=None):
    """Model selection of one conversation (agent/model_override.json; the primary group keeps the legacy top-level fields)
    + the guide shown as tooltips."""
    cur, until = "auto", ""
    try:
        o = json.load(open(_model_file(), encoding="utf-8"))
        node = o if not chat or chat == primary() else (o.get("groups") or {}).get(chat) or {}
        cur, until = node.get("model") or "auto", node.get("until") or ""
        if until and until < dt.datetime.now().isoformat(timespec="seconds"):
            cur, until = "auto", ""
    except Exception:
        pass
    try:
        guide = json.load(open(os.path.join(ROOT, "agent", "models.json"), encoding="utf-8"))
    except Exception:
        guide = {}
    return {"current": cur, "until": until, "last": kv(K("last_model", chat)),
            "recommend": (json.loads(kv(K("last_recommend", chat))) if kv(K("last_recommend", chat)) else None),
            "session_runs": kv(K("session_runs", chat)), "guide": guide}


def set_model(chat, model, hours=0):
    until = (dt.datetime.now() + dt.timedelta(hours=float(hours))).isoformat(timespec="seconds") if hours else ""
    try:
        o = json.load(open(_model_file(), encoding="utf-8"))
    except Exception:
        o = {}
    if not chat or chat == primary():
        o["model"], o["until"] = model, until
    else:
        o.setdefault("groups", {})[chat] = {"model": model, "until": until}
    dump_json(o, _model_file())


# ---------------- search over the whole history (own index, never touches the bridge's databases) ----------------
_search_lock = threading.Lock()


class _SearchDB:
    """sqlite connection + whether the FTS5 trigram index is available."""
    def __init__(self):
        self.con = sqlite3.connect(os.path.join(ROOT, "agent", "search.db"), timeout=30)
        self.con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        try:
            self.con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(text, chat UNINDEXED, sender UNINDEXED, ts UNINDEXED, kind UNINDEXED, ref UNINDEXED, tokenize='trigram')")
            self.fts = True
        except sqlite3.OperationalError:  # old SQLite without the trigram tokenizer: plain table + LIKE
            self.con.execute("CREATE TABLE IF NOT EXISTS docs (text, chat, sender, ts, kind, ref)")
            self.fts = False

    def __getattr__(self, name):
        return getattr(self.con, name)


def _sdb():
    return _SearchDB()


def search_sync():
    """Index messages and voice-note transcripts added since the last call (incremental)."""
    p = paths()
    with _search_lock:
        con = _sdb()
        try:
            for kind, db, sql, key in (
                ("msg", p["msg"], "SELECT rowid, chat_jid, sender, timestamp, content, id FROM messages WHERE rowid > ? AND content != '' ORDER BY rowid LIMIT 20000", "m_rowid"),
                ("voice", p["agent"], "SELECT rowid, chat_jid, sender, ts, text, id FROM transcripts WHERE rowid > ? AND text != '' ORDER BY rowid LIMIT 20000", "t_rowid"),
            ):
                while True:
                    last = int((con.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone() or ["0"])[0])
                    batch = rows(db, sql, (last,))
                    if not batch:
                        break
                    vals = [r for r in (list(b.values()) for b in batch)]
                    con.executemany("INSERT INTO docs (text, chat, sender, ts, kind, ref) VALUES (?, ?, ?, ?, ?, ?)",
                                    [(v[4], v[1], v[2], str(v[3]), kind, v[5]) for v in vals])
                    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(vals[-1][0])))
                    con.commit()
        finally:
            con.close()


def _trigrams(q):
    q = re.sub(r"\s+", " ", q.strip().lower())
    return list(dict.fromkeys(q[i:i + 3] for i in range(max(0, len(q) - 2))))


def _fuzzy(q, text):
    """Best similarity (0-1) between the query and any window of consecutive words of the text."""
    import difflib
    ql, words = q.lower(), text.lower().split()
    n = max(1, len(ql.split()))
    best = 0.0
    for w in (n, n + 1):
        for i in range(0, max(1, len(words) - w + 1)):
            best = max(best, difflib.SequenceMatcher(None, ql, " ".join(words[i:i + w])).ratio())
    return best


def _snippet(q, text, width=140):
    t = re.sub(r"\s+", " ", text)
    i = t.lower().find(q.lower())
    if i < 0:
        import difflib
        words, ql = t.split(" "), q.lower()
        best, bi = -1, 0
        pos = 0
        for w in words:
            r = difflib.SequenceMatcher(None, ql, w.lower()).ratio()
            if r > best:
                best, bi = r, pos
            pos += len(w) + 1
        i = bi
    a = max(0, i - width // 3)
    return ("…" if a else "") + t[a:a + width] + ("…" if a + width < len(t) else "")


def search(q, limit=12):
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": []}
    search_sync()
    con = _sdb()
    try:
        found = {}
        if len(q) < 3 or not con.fts:
            for r in con.execute("SELECT rowid, text, chat, sender, ts, kind, ref FROM docs WHERE text LIKE ? ORDER BY ts DESC LIMIT 60", (f"%{q}%",)):
                found[r[0]] = (1.0, r)
        else:
            phrase = '"' + q.replace('"', '""') + '"'
            for r in con.execute("SELECT rowid, text, chat, sender, ts, kind, ref FROM docs WHERE docs MATCH ? ORDER BY ts DESC LIMIT 60", (phrase,)):
                found[r[0]] = (1.0, r)
            if len(found) < 8:  # typos: any shared trigrams, re-ranked by similarity
                tri = _trigrams(q)[:14]
                if tri:
                    m = " OR ".join('"' + t.replace('"', '""') + '"' for t in tri)
                    for r in con.execute("SELECT rowid, text, chat, sender, ts, kind, ref FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT 120", (m,)):
                        if r[0] not in found:
                            sc = _fuzzy(q, r[1])
                            if sc >= 0.6:
                                found[r[0]] = (sc, r)
        nm = names()
        label = lambda j: nm.get(str(j).split("@")[0]) or str(j).split("@")[0]
        ranked = sorted(found.values(), key=lambda x: (x[0], str(x[1][4])), reverse=True)[:limit]
        out = [{"chat": r[2], "chat_name": label(r[2]), "sender": "אתה" if r[3] == "me" else label(r[3]), "ts": r[4], "kind": r[5], "ref": r[6],
                "score": round(sc, 2), "snippet": _snippet(q, r[1])} for sc, r in ranked]
        return {"results": out}
    finally:
        con.close()


def context(chat, ts, ref=None):
    """A few messages before and after a search hit, from the bridge's database."""
    p = paths()
    nm = names()
    label = lambda j: nm.get(str(j).split("@")[0]) or str(j).split("@")[0]
    before = rows(p["msg"], "SELECT id, sender, timestamp, content, media_type, is_from_me FROM messages WHERE chat_jid = ? AND timestamp <= ? ORDER BY timestamp DESC LIMIT 7", (chat, ts))[::-1]
    after = rows(p["msg"], "SELECT id, sender, timestamp, content, media_type, is_from_me FROM messages WHERE chat_jid = ? AND timestamp > ? ORDER BY timestamp LIMIT 6", (chat, ts))
    msgs = [dict(m, who=(label(m["sender"]) if not m["is_from_me"] else "אתה"), hit=bool(ref and m["id"] == ref) or m["timestamp"] == ts) for m in before + after]
    return {"chat": chat, "chat_name": label(chat), "messages": msgs}


def action(a):
    kind = a.get("do")
    p = paths()
    if kind == "model":
        m = a.get("model", "auto")
        set_model(a.get("chat"), m, a.get("hours") or 0)
        return {"ok": True, "model": m}
    if kind == "say_pc":  # a message typed in the dashboard: answered in the dashboard chat (sync=1: also posted to WhatsApp)
        chat = a.get("chat") or primary()
        if chat not in conversations():
            return {"error": "unknown conversation"}
        text = (a.get("text") or "").strip()
        files = [f for f in (a.get("attach") or []) if os.path.isfile(f)]
        if not text and not files:
            return {"error": "empty"}
        shown = text + "".join(f"\n📎 {os.path.basename(f)}" for f in files)
        sent = text + "".join(f"\n[קובץ מצורף: {f}]" for f in files)
        now = dt.datetime.now()
        con = agent_db_rw()
        try:
            con.execute("INSERT INTO local_messages (chat, at, role, text, files) VALUES (?, ?, 'me', ?, ?)",
                        (chat, now.isoformat(timespec="seconds"), shown.strip(), json.dumps([os.path.basename(f) for f in files], ensure_ascii=False)))
            con.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES (?, ?, ?, ?, 'pc', ?)",
                        (f"pc-{int(now.timestamp() * 1000)}", sent.strip(), now.strftime("%Y-%m-%d %H:%M"), chat, 1 if a.get("sync") else 0))
            con.commit()
        finally:
            con.close()
        hb = kv("heartbeat")
        alive = bool(hb) and (now - dt.datetime.fromisoformat(hb)).total_seconds() < 120
        return {"ok": True, "agent_alive": alive}
    if kind == "stop":  # the agent watches for this file and stops the running Claude
        open(os.path.join(ROOT, "agent", "stop.request"), "w").write(a.get("chat") or "")
        return {"ok": True}
    if kind == "groups_available":
        res = bridge("/list_groups", {}, timeout=40)
        have = set(conversations())
        return {"groups": [g for g in res.get("groups", []) if g["jid"] not in have], "error": res.get("error")}
    if kind in ("group_connect", "group_create"):
        purpose = a.get("purpose") if a.get("purpose") in ("dev", "general", "4m") else "general"
        name = (a.get("name") or "").strip()
        jid = a.get("jid")
        if kind == "group_create":
            if not name or len(name) > 100:
                return {"error": "שם בין 1 ל-100 תווים"}
            res = bridge("/create_group", {"name": name, "participants": [], "description": "השיחה הפרטית שלך עם Claude"}, timeout=40)
            jid = res.get("jid")
            if not str(jid or "").endswith("@g.us"):
                return res if "error" in res else {"error": "create failed"}
        if not str(jid or "").endswith("@g.us"):
            return {"error": "bad group"}
        if kind == "group_connect" and not a.get("confirm_members"):  # Claude's answers go to everyone in the group
            info = bridge("/group_info", {"jid": jid}, timeout=40)
            if info.get("members", 1) > 1:
                return {"needs_confirm": True, "members": info["members"]}
        slug = re.sub(r"[^0-9a-zA-Z]+", "", jid.split("@")[0])[:12] or "group"
        rel = f"groups/{slug}.md"
        dst = os.path.join(ROOT, "agent", "home", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            tpl = os.path.join(ROOT, "agent", "group_templates", f"{purpose}.md")
            body = open(tpl, encoding="utf-8").read() if os.path.exists(tpl) else "# הוראות לשיחה\n"
            open(dst, "w", encoding="utf-8").write(body.replace("{owner}", cfg().get("owner_name") or "הבעלים"))
        raw = load_groups_raw()
        raw[jid] = {"name": name or a.get("label") or jid, "purpose": purpose, "instructions": rel, "slug": slug,
                    "budget_usd": float(cfg().get("daily_budget_usd", 5)), "enabled": True}
        save_groups_raw(raw)
        if purpose == "dev":
            set_model(jid, "opus")
        sync_policy_groups()
        return {"ok": True, "jid": jid}
    if kind == "group_disconnect":
        jid = a.get("jid")
        if jid in (primary(), LOCAL):
            return {"error": "cannot disconnect this one"}
        raw = load_groups_raw()
        raw.pop(jid, None)
        save_groups_raw(raw)
        sync_policy_groups()
        return {"ok": True}
    if kind == "group_budget":
        jid = a.get("jid") or primary()
        try:
            b = float(a.get("budget"))
        except (TypeError, ValueError):
            return {"error": "bad number"}
        if not 0 < b <= 1000:
            return {"error": "0-1000"}
        raw = load_groups_raw()
        raw.setdefault(jid, {})["budget_usd"] = b
        save_groups_raw(raw)
        return {"ok": True}
    if kind in ("listen_pause", "listen_resume"):  # everything (no chat) or one conversation
        chat = a.get("chat")
        key = K("listen_paused", chat) if chat else "listen_paused"
        kv_put(key, dt.datetime.now().isoformat(timespec="seconds") if kind == "listen_pause" else "")
        return {"ok": True}
    if kind in ("cmd_cancel", "cmd_retry"):
        con = agent_db_rw()
        try:
            if kind == "cmd_cancel":
                n = con.execute("UPDATE commands SET status = 'cancelled', answered = ? WHERE id = ? AND status IN ('pending','waiting_limit','interrupted')",
                                (dt.datetime.now().isoformat(timespec="seconds"), int(a["id"]))).rowcount
            else:
                n = con.execute("UPDATE commands SET status = 'pending', retry_at = NULL WHERE id = ? AND status IN ('interrupted','waiting_limit','cancelled')",
                                (int(a["id"]),)).rowcount
            con.commit()
        finally:
            con.close()
        return {"ok": n == 1} if n == 1 else {"error": "לא ניתן (ייתכן שהפקודה כבר רצה — השתמש בעצור)"}
    if kind == "cmd_clear":  # cancel everything still waiting in one conversation (a running answer is stopped with ⛔)
        chat = a.get("chat") or primary()
        con = agent_db_rw()
        try:
            n = con.execute("UPDATE commands SET status = 'cancelled', answered = ? WHERE status IN ('pending','waiting_limit','interrupted') "
                            "AND coalesce(chat_jid, ?) = ?", (dt.datetime.now().isoformat(timespec="seconds"), primary(), chat)).rowcount
            con.commit()
        finally:
            con.close()
        return {"ok": True, "cancelled": n}
    if kind == "sched_cancel":
        con = agent_db_rw()
        try:
            n = con.execute("UPDATE scheduled_drafts SET status = 'cancelled', done = ? WHERE id = ? AND status = 'scheduled'",
                            (dt.datetime.now().isoformat(timespec="seconds"), int(a["id"]))).rowcount
            con.commit()
        finally:
            con.close()
        return {"ok": True} if n else {"error": "not scheduled"}
    if kind in ("rem_cancel", "rem_snooze"):
        con = agent_db_rw()
        try:
            if kind == "rem_cancel":
                n = con.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'active'", (int(a["id"]),)).rowcount
            else:
                m = int(float(a["minutes"] if a.get("minutes") not in (None, "") else 60))
                if not 1 <= m <= 7 * 1440:
                    return {"error": "1-10080"}
                due = (dt.datetime.now().astimezone() + dt.timedelta(minutes=m)).isoformat(timespec="seconds")
                n = con.execute("UPDATE reminders SET due = ?, status = 'active' WHERE id = ?", (due, int(a["id"]))).rowcount
            con.commit()
        finally:
            con.close()
        return {"ok": True} if n else {"error": "not found"}
    if kind == "read_all":  # 🛞 permanent read-aloud for one conversation (same as /🛞 in WhatsApp)
        chat = a.get("chat") or primary()
        if chat not in conversations():
            return {"error": "unknown conversation"}
        kv_put(K("read_all", chat), "1" if a.get("on") else "")
        return {"ok": True, "on": bool(a.get("on"))}
    if kind == "session_new":  # the agent closes the session locally (summary to handoff), like "/סשן חדש" in WhatsApp
        chat = a.get("chat") or primary()
        if chat not in conversations():
            return {"error": "unknown conversation"}
        now = dt.datetime.now()
        con = agent_db_rw()
        try:
            con.execute("INSERT INTO commands (msg_id, text, at, chat_jid, source, sync) VALUES (?, '/סשן חדש', ?, ?, 'pc', 0)",
                        (f"pc-{int(now.timestamp() * 1000)}", now.strftime("%Y-%m-%d %H:%M"), chat))
            con.commit()
        finally:
            con.close()
        return {"ok": True}
    if kind == "group_enable":  # a connected group switched off (enabled: false) is not listened to; switch it back on
        raw = load_groups_raw()
        if a.get("jid") not in raw:
            return {"error": "unknown group"}
        raw[a["jid"]]["enabled"] = bool(a.get("on", True))
        save_groups_raw(raw)
        sync_policy_groups()
        return {"ok": True}
    if kind == "settings_save":
        c = cfg()
        was_on = bool(c.get("transcribe", True))
        try:
            for k, v in (a.get("values") or {}).items():
                if k in SETTINGS:
                    c[k] = validate_setting(k, v)
        except ValueError as e:
            return {"error": str(e)}
        save_cfg(c)
        now_on = bool(c.get("transcribe", True))
        if was_on and not now_on and not kv("transcribe_off_since"):  # remember the gap, to fill it when switched back on
            kv_put("transcribe_off_since", dt.datetime.now().isoformat(timespec="seconds"))
        if a.get("restart"):
            open(os.path.join(ROOT, "agent", "restart.request"), "w", encoding="utf-8").write("settings changed in the dashboard")
        return {"ok": True}
    if kind == "transcribe_backfill":  # the agent picks this up within a minute and transcribes the gap on its slow queue
        if not cfg().get("transcribe", True):
            return {"error": "התמלול כבוי — הפעל אותו בהגדרות (ושמור והפעל מחדש) ואז תמלל את החסרים"}
        kv_put("transcribe_backfill_request", dt.datetime.now().isoformat(timespec="seconds"))
        return {"ok": True}
    if kind == "history_sync":  # ask the phone for older messages of one chat (they arrive asynchronously)
        jid = str(a.get("jid") or "")
        if not re.fullmatch(r"[\w.\-]+@(s\.whatsapp\.net|g\.us|lid)", jid):
            return {"error": "bad chat"}
        res = bridge("/history_sync", {"jid": jid, "count": max(10, min(500, int(a.get("count") or 50)))}, timeout=20)
        return res if res.get("error") else {"ok": True, **res}
    if kind == "peer_backup_now":
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        subprocess.Popen([uv, "run", "-q", "--no-sync", "python", "-X", "utf8", "agent.py", "peer_backup"],
                         cwd=os.path.join(ROOT, "agent"), creationflags=NW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}
    if kind == "plugin_toggle":
        c = cfg()
        on, off, x = list(c.get("plugins", [])), list(c.get("plugins_disabled", [])), a.get("path")
        if x not in on + off:
            return {"error": "unknown plugin"}
        on, off = ([y for y in on if y != x] + ([x] if a.get("on") else [])), ([y for y in off if y != x] + ([] if a.get("on") else [x]))
        c["plugins"], c["plugins_disabled"] = on, off
        save_cfg(c)
        open(os.path.join(ROOT, "agent", "restart.request"), "w", encoding="utf-8").write("plugin switched in the dashboard")
        return {"ok": True}
    if kind == "peers_save":
        out = []
        for pr in a.get("peers") or []:
            name, url, tun = str(pr.get("name", "")).strip()[:40], str(pr.get("url", "")).strip(), str(pr.get("tunnel", "") or "").strip()
            sh = str(pr.get("ssh", "") or "").strip()
            ok_alias = lambda x: not x or re.fullmatch(r"[\w.@\-]{1,64}", x)
            if not name or not re.fullmatch(r"http://127\.0\.0\.1:\d{2,5}", url) or not ok_alias(tun) or not ok_alias(sh):
                return {"error": "שם, כתובת http://127.0.0.1:<פורט>, מנהרה ו-ssh (לא חובה)"}
            out.append({"name": name, "url": url, **({"tunnel": tun} if tun else {}), **({"ssh": sh} if sh else {})})
        c = cfg()
        c["peers"] = out
        save_cfg(c)
        open(os.path.join(ROOT, "agent", "restart.request"), "w", encoding="utf-8").write("computers list changed in the dashboard")
        return {"ok": True}
    if kind == "tunnel":
        kv_put(f"tunnel_off:{a.get('name', '')}", "" if a.get("on") else "1")
        return {"ok": True}
    if kind == "service":  # really stop / start the bridge or the agent (restart = "restart")
        what, op = a.get("what"), a.get("op")
        if what not in TASKS or op not in ("stop", "start"):
            return {"error": "bad service"}
        if op == "stop" and what == "agent":  # this server may live inside the agent: answer first
            threading.Timer(1.0, svc_stop, args=("agent",)).start()
        elif op == "stop":
            svc_stop(what)
        else:
            svc_start(what)
        return {"ok": True}
    if kind == "peer_ssh_start":
        return peer_ssh_start(int(a.get("i", -1)))
    if kind == "selftest_now":
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        subprocess.Popen([uv, "run", "-q", "--no-sync", "python", "-X", "utf8", os.path.join(ROOT, "tools", "selftest.py"), "--quiet"],
                         cwd=os.path.join(ROOT, "agent"), creationflags=NW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}
    if kind == "search":
        return search(a.get("q"))
    if kind == "context":
        return context(a.get("chat"), a.get("ts"), a.get("ref"))
    if kind == "say":  # continue the conversation from the dashboard: a normal message from the owner in the control group
        text = (a.get("text") or "").strip()
        if not text and not a.get("media_path"):
            return {"error": "empty"}
        body = {"recipient": a.get("chat") if a.get("chat") in conversations() and a.get("chat") != LOCAL else cfg()["claude_group"], "message": text}
        if a.get("media_path") and os.path.isfile(a["media_path"]):
            body["media_path"] = a["media_path"]
        res = bridge("/send", body, timeout=60)  # the bridge paces sends: 8 s was too short, the app showed an error and the owner re-sent
        return res if "error" in res else {"ok": True}
    if kind == "group_info":  # the live name of a connected group (dashboard card)
        g = a.get("jid") if a.get("jid") in conversations() and a.get("jid") != LOCAL else cfg().get("claude_group", "")
        res = bridge("/group_info", {"jid": g}) if g else {"error": "no control group"}
        res["jid"] = g
        return res
    if kind == "group_rename":  # rename the control group (owner asked from the dashboard)
        name = (a.get("name") or "").strip()
        if not name or len(name) > 100:
            return {"error": "שם בין 1 ל-100 תווים"}
        jid = a.get("jid") if a.get("jid") in conversations() and a.get("jid") != LOCAL else cfg()["claude_group"]
        res = bridge("/set_group", {"jid": jid, "name": name})
        raw = load_groups_raw()
        if jid in raw:
            raw[jid]["name"] = name
            save_groups_raw(raw)
        return res
    if kind == "draft_decide":
        res = bridge("/draft_decide", {"id": int(a["id"]), "approve": bool(a.get("approve"))})
        if "error" in res and not a.get("approve"):  # bridge down: cancelling needs no bridge
            return action({"do": "draft", "id": a["id"], "status": "cancelled"})
        return res
    if kind in ("pause", "resume"):
        body = {}
        if kind == "pause":
            body = {"paused_until": (dt.datetime.now().astimezone() + dt.timedelta(hours=float(a.get("hours", 6)))).isoformat(timespec="seconds"),
                    "pause_reason": "הושהה ידנית מלוח הבקרה"}
        else:
            body = {"paused_until": "", "pause_reason": ""}
        res = bridge("/policy", body)
        if "error" in res:  # bridge down: write the policy file directly; the bridge reads it on every send
            pol = local_policy()
            pol.update(body)
            pol["mode"], pol["auto_until"], pol["allow"] = "draft", "", None  # no auto mode, no allow-list
            dump_json(pol, p["policy"], ensure_ascii=False, indent=2)
            res = pol
        return res
    if kind == "draft":
        if a["status"] not in ("cancelled", "discarded", "sent"):
            return {"error": "bad status"}
        con = sqlite3.connect(p["msg"], timeout=10)
        con.execute("UPDATE drafts SET status = ? WHERE id = ?", (a["status"], int(a["id"])))
        con.commit()
        con.close()
        return {"ok": True}
    if kind == "backup":
        import shutil
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        subprocess.Popen([uv, "run", "-q", "--no-sync", "python", "-X", "utf8", "agent.py", "backup"],
                         cwd=os.path.join(ROOT, "agent"), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": True}
    if kind == "restart":
        if a.get("what") == "all":  # bridge first; the agent (which may host this server) a moment later, after we answer
            import threading
            kill("whatsapp-bridge.exe")
            threading.Timer(1.5, kill, args=("agent.py",)).start()
            return {"ok": True}
        kill({"bridge": "whatsapp-bridge.exe", "agent": "agent.py"}[a["what"]])
        return {"ok": True}
    if kind == "voice_test":
        voice = a.get("voice") if a.get("voice") in VOICES else VOICES[0]
        lf = os.path.join(ROOT, "agent", "home", "local_files")
        os.makedirs(lf, exist_ok=True)
        name = f"voice_test_{int(dt.datetime.now().timestamp())}.ogg"
        txt = os.path.join(lf, name + ".txt")
        open(txt, "w", encoding="utf-8").write(a.get("text") or ("שלום, זה הקול שלי בתשובות הקוליות." if voice.startswith("he") else "Hello, this is my voice for voice replies."))
        py = os.path.join(ROOT, "agent", ".venv", "Scripts", "python.exe")
        r = subprocess.run([py if os.path.exists(py) else sys.executable, "-X", "utf8", os.path.join(ROOT, "agent", "agent.py"), "tts", voice,
                            os.path.join(lf, name), txt], cwd=os.path.join(ROOT, "agent"), capture_output=True, text=True, timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            os.remove(txt)
        except OSError:
            pass
        if not os.path.exists(os.path.join(lf, name)):
            return {"error": (r.stderr or r.stdout or "tts failed")[-300:]}
        return {"ok": True, "url": "/lf/" + name}
    if kind == "tool_update_check":
        import time
        cache = os.path.join(ROOT, "agent", "update_check.json")
        if not a.get("force") and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 6 * 3600:
            return json.load(open(cache, encoding="utf-8"))
        import shutil
        uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv.exe")
        out = subprocess.run([uv, "run", "-q", "--no-project", "python", "-X", "utf8", os.path.join(ROOT, "tools", "update.py"), "--check"],
                             capture_output=True, text=True, encoding="utf-8", timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        res = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {"error": out.stderr[-300:]}
        if "error" not in res:  # don't remember a failed check (offline, release not out yet)
            json.dump(res, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
        return res
    if kind == "tool_update_apply":
        # visible window so the user can follow the update; it stops and restarts the bridge and the agent
        subprocess.Popen(["cmd.exe", "/c", "start", "Update", os.path.join(ROOT, "update.cmd")], cwd=ROOT)
        return {"ok": True}
    if kind in ("git_status", "git_commit", "release"):
        return git_action(kind, a)
    if kind == "update_check":
        from dash import updater
        return updater.check()
    if kind == "update_apply":
        from dash import updater
        return updater.apply()
    return {"error": "unknown action"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_bytes(self, b, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8", code)

    def _peer_index(self, path):
        m = re.match(r"^/peer/(\d+)(/.*)$", path)
        return (int(m.group(1)), m.group(2)) if m else (None, None)

    def do_GET(self):
        path = self.path.split("?")[0]
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if path == "/brief":
            try:
                return self.send_json(brief())
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        if path == "/peers":
            try:
                return self.send_json(peers_status())
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        pi, rest = self._peer_index(path)
        if pi is not None:  # the other machine's dashboard, through the tunnel (data / brief / its files)
            q = urllib.parse.urlsplit(self.path).query
            if rest.startswith("/lf/"):
                try:
                    r = urllib.request.urlopen(peer_url(pi) + rest, timeout=15)
                    body = r.read()
                    return self.send_bytes(body, r.headers.get("Content-Type", "application/octet-stream"))
                except Exception:
                    return self.send_bytes(b"not found", "text/plain", 404)
            if rest not in ("/data", "/brief"):
                return self.send_json({"error": "not allowed"}, 404)
            return self.send_json(peer_call(pi, rest + (("?" + q) if q else ""), timeout=8))
        if path == "/data":
            try:
                return self.send_json(data((qs.get("chat") or [None])[0]))
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        if path.startswith("/lf/"):  # files Claude returned in a PC-only conversation
            name = os.path.basename(urllib.parse.unquote(path[4:]))
            f = os.path.join(ROOT, "agent", "home", "local_files", name)
            if not name or not os.path.isfile(f):
                return self.send_bytes(b"not found", "text/plain", 404)
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(f)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(os.path.getsize(f)))
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(name))
            self.end_headers()
            with open(f, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
            return
        rel = "index.html" if path in ("/", "") else path.lstrip("/").replace("static/", "", 1)
        f = os.path.normpath(os.path.join(STATIC, rel))
        if not f.startswith(os.path.normpath(STATIC)) or not os.path.isfile(f):
            return self.send_bytes(b"not found", "text/plain", 404)
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_bytes(open(f, "rb").read(), ctype)

    def do_POST(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{host}", f"https://{host}"):  # same-origin only (https = via Tailscale)
            return self.send_json({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length", 0))
        pi, rest = self._peer_index(self.path.split("?")[0])
        if pi is not None:  # actions / uploads for the other machine
            body = self.rfile.read(n) if n else b""
            if rest == "/action":
                return self.send_json(peer_call(pi, "/action", raw=body or b"{}", timeout=60))
            if rest == "/upload":
                q = urllib.parse.urlsplit(self.path).query
                return self.send_json(peer_call(pi, "/upload" + (("?" + q) if q else ""), raw=body, ctype="application/octet-stream", timeout=60))
            return self.send_json({"error": "not allowed"}, 404)
        if self.path.startswith("/upload"):  # a file pasted / chosen in the chat box -> agent/home/inbox
            name = os.path.basename(urllib.parse.unquote((urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("name") or ["file"])[0])) or "file"
            if n > 25 * 1024 * 1024:
                return self.send_json({"error": "file too large (25 MB max)"}, 413)
            d = os.path.join(ROOT, "agent", "home", "inbox")
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, f"{dt.datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^0-9A-Za-z._-]+', '_', name)[:60] or 'file'}")
            with open(dst, "wb") as fh:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    fh.write(chunk)
                    left -= len(chunk)
            return self.send_json({"ok": True, "path": dst, "name": name})
        try:
            return self.send_json(action(json.loads(self.rfile.read(n) or b"{}")))
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)


def serve(port=8770):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.serve_forever()


def start_free_port():
    """For the desktop app: start on any free localhost port, return it."""
    import threading
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]
