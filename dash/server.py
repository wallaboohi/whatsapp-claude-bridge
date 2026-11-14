"""Dashboard backend of the WhatsApp ↔ Claude tool.

Standalone: reads the tool's local databases directly (read-only) and talks to the bridge when it is up, so the
dashboard works without the agent, without Claude and without internet. Served by the desktop app (WhatsAppDash.exe)
and by the agent on http://127.0.0.1:8770. Bound to localhost only.
"""
import datetime as dt
import json
import glob
import mimetypes
import os
import sqlite3
import subprocess
import sys
import re
import shutil
import queue
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
try:  # a local, not-yet-released build says which commit it was built from (dash/BUILD, written by the build; the version number itself stays 1.1.x)
    BUILD = open(os.path.join(PKG, "BUILD")).read().strip()
except OSError:
    BUILD = ""
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
    "session_max_runs": ("int", 60, (1, 1000)), "claude_timeout_minutes": ("int", 0, (0, 1440)), "disconnect_alert_minutes": ("int", 10, (1, 1440)),
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


def read_clipboard():
    """This PC's clipboard as text (Windows CF_UNICODETEXT), "" when it holds no text."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = wintypes.HANDLE
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    for _ in range(5):  # another program may hold the clipboard for a moment
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return ""
    try:
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


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
            "host_label": c.get("host_label") or "", "account": c.get("claude_account") or "", "git": git_state(), "ai": ai_brief()}


def _first_doc_line(path):
    """First line of a tool's docstring: what it is for (system map)."""
    try:
        txt = open(path, encoding="utf-8").read(3000)
        m = re.search(r'"""(.+?)(?:\n\n|""")', txt, re.S)
        return (m.group(1).strip() if m else "")[:140]
    except OSError:
        return ""


TOOL_HE = {"gmail.py": "מייל: חיפוש, קריאה, קבצים מצורפים וטיוטות (בלי שליחה)", "gsheet.py": "קריאת גיליון מקטע D",
           "gsheet_write.py": "כתיבה לגיליון — רק אחרי אישור", "migrate_lid.py": "איחוד שיחות LID למספר טלפון (חד-פעמי)",
           "phone_sync.py": "תיקיית הטלפון וקבצים ישנים מהטלפון", "release.py": "שחרור גרסה של הכלי",
           "release_dash.py": "בניית הלוח", "selftest.py": "בדיקה עצמית מלאה", "setup.py": "אשף התקנה",
           "update.py": "עדכון אוטומטי עם חזרה לאחור", "wa_admin.py": "פעולות ניהול בגשר (קבוצות, תמונות)",
           "wa_draft_cancel.py": "ביטול טיוטות", "wa_history.py": "טעינת היסטוריה ישנה מהטלפון",
           "wa_push.py": "התראה לטלפון", "wa_remind.py": "תזכורות", "wa_rich.py": "אירוע, תיוג, מיקום",
           "wa_send.py": "שליחה (טיוטה לאנשים אחרים)", "install_dash.ps1": "התקנת הלוח"}


def map_data():
    """The data stores on the map (also the only paths the map may open in Explorer)."""
    c = cfg()
    home = os.path.join(ROOT, "agent", "home")
    return [{"name": "messages.db", "what": "כל הודעות הווצאפ, קבוצות, סקרים, טיוטות", "path": paths()["msg"]},
            {"name": "agent.db", "what": "פקודות, תמלולים, עלויות, שיחות מקומיות", "path": paths()["agent"]},
            {"name": "whatsapp.db", "what": "אנשי קשר (השמות מהטלפון), LID ↔ מספר", "path": paths()["wa"]},
            {"name": "תיקיית הבית", "what": "handoff, מילון תמלול, נהלים (ציפורה), קבצים משותפים", "path": home},
            {"name": "תיקיית הטלפון", "what": "עותק יומי של מדיה ומסמכים מהטלפון", "path": os.path.expanduser((c.get("phone") or {}).get("data_dir") or "~/phone-data")},
            {"name": "גיבוי יומי", "what": "גיבוי 03:00", "path": backup_dir_abs()},
            {"name": "hub (קוד משותף)", "what": "מקור האמת לקוד, בלפטופ", "path": ""},
            {"name": "GitHub", "what": "פרסום יומי 23:30", "path": ""}]


# ---------------- legend: groups ⇄ bridge ⇄ model ⇄ provider (owner 13.11) ----------------
# The provider drives the bridge, not a group: every connected group follows the bridge's provider. Each layer is handled on
# its own, each node clickable. Same data as WhatsApp (/ספק, /מודל) — the "phone" line of each node says which command.

def _providers():
    try:
        out = json.load(open(os.path.join(ROOT, "agent", "providers.json"), encoding="utf-8"))
    except Exception:
        out = {}
    for k, v in (cfg().get("providers") or {}).items():
        out.setdefault(k, {}).update(v)
    return out


def _provider_file():
    return os.path.join(ROOT, "agent", "provider.json")


def _provider_state():
    try:
        o = json.load(open(_provider_file(), encoding="utf-8"))
    except Exception:
        o = {}
    return o if isinstance(o, dict) else {}


def active_provider(chat=None):
    """(name, model) answering in this conversation: its own choice (provider.json "groups", owner 14.11) or the computer's."""
    o = _provider_state()
    g = (o.get("groups") or {}).get(chat) if chat else None
    if g and g.get("provider") in _providers():
        return g["provider"], g.get("model") or ""
    name = o.get("provider") if o.get("provider") in _providers() else "claude"
    return name, o.get("model") or ""


def _provider_key(p):
    if not p.get("key_file"):
        return ""
    try:
        return open(os.path.join(ROOT, "secrets", p["key_file"]), encoding="utf-8").read().strip()
    except OSError:
        return ""


_or_free = {"at": 0.0, "ids": []}


def openrouter_free_models():
    """The :free models of OpenRouter that support tools, from its public catalog (cached 10 minutes); the last list (or []) when it cannot be read."""
    if time.time() - _or_free["at"] < 600 and _or_free["ids"]:
        return _or_free["ids"]
    try:
        data = json.loads(urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=15).read().decode())["data"]
    except Exception:
        return _or_free["ids"]
    free = [m for m in data if str(m.get("id", "")).endswith(":free") and "tools" in (m.get("supported_parameters") or [])
            and str((m.get("pricing") or {}).get("prompt")) == "0" and str((m.get("pricing") or {}).get("completion")) == "0"]
    _or_free.update(at=time.time(), ids=[m["id"] for m in sorted(free, key=lambda m: -(m.get("context_length") or 0))])
    return _or_free["ids"]


def anthropic_probe(p, tok):
    """{"ok", "detail", "models"} for an Anthropic-compatible cloud API with a fixed model list (providers.json "models"): one 1-token call
    proves the key (owner 13.11: the DeepSeek key must be the key itself, not its id)."""
    models = list(p["models"])
    body = {"model": p.get("model") or models[0], "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
    req = urllib.request.Request(p["base_url"].rstrip("/") + "/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": tok, "Authorization": "Bearer " + tok, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return {"ok": True, "detail": f"מחובר · המפתח תקין · {len(models)} מודלים", "models": models}
    except urllib.error.HTTPError as e:
        why = {401: "המפתח נדחה (ודא שהעתקת את המפתח עצמו ולא את המזהה שלו)", 403: "המפתח נדחה", 402: "אין יתרה בחשבון הספק",
               429: "הספק מגביל קצב כרגע"}.get(e.code, f"שגיאה {e.code} מהספק")
        return {"ok": False, "detail": why, "models": models}
    except Exception as e:
        return {"ok": False, "detail": f"לא מגיב: {str(e)[:70]}", "models": models}


def provider_check(name):
    """Reachable now + its models (no secret ever leaves this function)."""
    p = _providers().get(name)
    if not p:
        return {"ok": False, "detail": "ספק לא מוכר", "models": []}
    if p.get("kind") == "auto":  # the ladder, cheapest first (live readiness is shown by /ספק in WhatsApp)
        allp = _providers()
        rows = [f"{allp[n].get('short') or n} ({allp[n].get('cost') or ''})" for n in (p.get("ladder") or []) if n in allp and n != "auto"]
        return {"ok": True, "detail": "מהזול ליקר: " + " ← ".join(rows), "models": []}
    if p.get("kind") == "claude":  # owner 14.11: the real sign-in and the quota, so he can see it and renew it himself
        models = [k for k in json.load(open(os.path.join(ROOT, "agent", "models.json"), encoding="utf-8")) if k != "auto"]
        try:
            acc = claude_account_info()
        except Exception:
            acc = {}
        lim = kv("limit_until") or ""
        capped = bool(lim) and lim > dt.datetime.now().isoformat(timespec="seconds")
        if not acc.get("logged_in"):
            return {"ok": False, "detail": "לא מחובר: לחץ \"התחבר מחדש\"", "models": models, "login": True}
        return {"ok": not capped, "detail": f"מחובר · {acc.get('email') or ''}" + (f" · {acc['plan']}" if acc.get("plan") else "")
                + (f" · ⏸️ המכסה נגמרה עד {lim[11:16]}" if capped else ""), "models": models, "login": True}
    if p.get("kind") == "cli":  # owner 14.11: an agent CLI of this computer (Gemini CLI, Copilot, Codex): installed + signed in, no request spent
        c = p.get("cli") or {}
        exe = shutil.which(c.get("bin") or "")
        if not exe:
            return {"ok": False, "detail": f"{c.get('bin')} לא מותקן במחשב הזה", "models": list(p.get("models") or [])}
        login = {"copilot": "~/.copilot", "codex": "~/.codex/auth.json", "gemini": "~/.gemini/oauth_creds.json"}.get(c.get("bin"), "")
        signed = bool(login) and os.path.exists(os.path.expanduser(login))
        keyed = all(_provider_key({"key_file": f}) for f in (c.get("key_env") or {}).values()) if c.get("key_env") else False
        if not signed and not keyed:
            return {"ok": False, "detail": f"מותקן, לא מחובר: הרץ {c.get('bin')} בחלון והתחבר", "models": list(p.get("models") or [])}
        return {"ok": True, "detail": "מותקן · " + ("מחובר לחשבון" if signed else "עובד עם המפתח השמור"), "models": list(p.get("models") or [])}
    if p.get("kind") == "opencode":  # the OpenCode program on this computer and its models (free Zen + Ollama); no request is spent
        exe = cfg().get("opencode_path") or shutil.which("opencode") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "ai.opencode.desktop", "resources", "opencode-cli.exe")
        if not exe or not os.path.isfile(exe):
            return {"ok": False, "detail": "OpenCode לא מותקן במחשב הזה", "models": []}
        try:
            r = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, creationflags=_NOWIN)
            models = [x.strip() for x in r.stdout.splitlines() if "/" in x and not x.strip().startswith("ollama/")]
        except Exception as e:
            return {"ok": False, "detail": "OpenCode לא עונה: " + str(e)[:60], "models": []}
        return {"ok": bool(models), "detail": f"מותקן · {len(models)} מודלים (חינמיים: {sum(1 for m in models if 'free' in m)})", "models": models}
    if p.get("kind") == "anthropic-key":
        k = _provider_key(p)
        return {"ok": bool(k), "detail": "מפתח שמור" if k else "חסר מפתח", "models": ["opus", "sonnet", "haiku"]}
    tok = p.get("token") or _provider_key(p)
    if not tok:
        return {"ok": False, "detail": "חסר מפתח", "models": []}
    if p.get("free_list") == "openrouter":  # no call that spends quota: the live catalog is the list
        ids = openrouter_free_models() or list(p.get("models") or [])
        return {"ok": bool(ids), "detail": f"מפתח שמור · {len(ids)} מודלים חינמיים עם כלים", "models": ids}
    if p.get("probe") == "ollama":  # every host in order (laptop = default, desktop = fallback): which serves now, which are down
        rows, pick = [], None
        for h in p.get("hosts") or []:
            try:
                names = [m["name"] for m in json.loads(urllib.request.urlopen(h["url"].rstrip("/") + "/api/tags", timeout=4).read().decode()).get("models", [])]
                rows.append(f"{h.get('label') or h['url']} ✅")
                pick = pick or (h, names)
            except Exception:
                rows.append(f"{h.get('label') or h['url']} ⚪")
        if not pick:
            return {"ok": False, "detail": "אף מחשב לא מגיב: " + " · ".join(rows), "models": []}
        return {"ok": True, "detail": f"עובד דרך {pick[0].get('label') or pick[0]['url']} · " + " · ".join(rows), "models": pick[1]}
    if p.get("models") and p.get("base_url"):
        return anthropic_probe(p, tok)
    try:
        if "11434" in p.get("base_url", ""):
            d = json.loads(urllib.request.urlopen(p["base_url"] + "/api/tags", timeout=6).read().decode())
            models = [m["name"] for m in d.get("models", [])]
        else:
            req = urllib.request.Request(p["base_url"].rstrip("/") + "/v1/models", headers={"Authorization": "Bearer " + tok})
            models = [m.get("id") for m in json.loads(urllib.request.urlopen(req, timeout=10).read().decode()).get("data", []) if m.get("id")]
        return {"ok": True, "detail": f"מחובר · {len(models)} מודלים", "models": models}
    except Exception as e:
        return {"ok": False, "detail": "לא מגיב: " + str(e)[:70], "models": []}


_CHK = {}


def provider_check_cached(name, ttl=120):
    """provider_check for the legend and the front-page chip: they refresh every minute, and a check can be a real 1-token call to a
    free API (quota): at most one per provider per `ttl` seconds. Saving a key or switching forgets the cache."""
    hit = _CHK.get(name)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    res = provider_check(name)
    _CHK[name] = (time.time(), res)
    return res


def legend(chat=None):
    """Everything the legend page draws, from the live state. With chat: "active" = that conversation's AI (its own or the computer's)."""
    c = cfg()
    convs = conversations()
    groups = []
    for jid, v in convs.items():
        if jid == LOCAL:
            continue
        mi = model_info(jid)
        groups.append({"jid": jid, "name": v.get("name") or jid, "purpose": v.get("purpose") or "",
                       "primary": jid == primary(), "guest": bool(v.get("guest")), "model": mi["current"],
                       "code": "agent/groups.json · " + (v.get("instructions") or "groups/*.md"),
                       "phone": "בקבוצה עצמה: /מודל · /סשן · /עצור · /תור"})
    pname, pmodel = active_provider(chat if chat in convs else None)
    gname, gmodel = active_provider()
    own = bool(chat) and pname is not None and ((_provider_state().get("groups") or {}).get(chat) or {}).get("provider") in _providers()
    provs = []
    for k, p in _providers().items():
        chk = provider_check_cached(k)
        provs.append({"id": k, "label": p.get("label") or k, "kind": p.get("kind"), "how": p.get("how") or "",
                      "short": p.get("short") or "", "cost": p.get("cost") or "",
                      "where": "local" if (p.get("where") == "local" or p.get("hosts")) else "remote",
                      "active": k == pname, "ok": chk["ok"], "detail": chk["detail"], "models": chk["models"][:60],
                      "model": (pmodel or p.get("model") or "") if k == pname else (p.get("model") or ""),
                      "key_file": p.get("key_file") or "", "has_key": bool(_provider_key(p)) if p.get("key_file") else None})
    mi = model_info(primary())
    return {"at": dt.datetime.now().isoformat(timespec="seconds"),
            "user": {"name": c.get("owner_name") or "הבעלים", "phone": "קבוצת הבקרה בווצאפ"},
            "bridge": {"machine": c.get("host_label") or socket.gethostname(), "code": "agent/agent.py · whatsapp-bridge/",
                       "phone": "/ספק · /מודל · /תקרה · /הפעל מחדש"},
            "groups": groups,
            "model": {"current": mi["current"] if pname == "claude" else (pmodel or (_providers().get(pname) or {}).get("model") or ""),
                      "provider": pname, "guide": {k: {"he": v.get("he"), "tip": v.get("tip"), "cost": v.get("cost")} for k, v in (mi.get("guide") or {}).items()},
                      "code": "agent/model_override.json · models.json" if pname == "claude" else "agent/provider.json",
                      "phone": "/מודל <שם> (או /עצור <שם> באמצע עבודה)" if pname == "claude" else f"/ספק {pname} <מודל>"},
            "providers": provs,
            "scope": {"chat": chat or "", "chat_name": (convs.get(chat) or {}).get("name") or "", "own": own,
                      "computer": {"provider": gname, "short": (_providers().get(gname) or {}).get("short") or gname}},
            "provider_code": "agent/providers.json · agent/provider.json · secrets/<key>",
            "provider_phone": "/ספק · /ספק <שם> [מודל]"}


_CLAUDE_ACC = {"at": 0.0, "v": None}
_LOGIN = {"proc": None, "url": "", "at": 0.0}
LOGIN_URL_RE = re.compile(r"https://\S*oauth/authorize\S*")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def claude_account_info(max_age=60, force=False):
    """Owner 14.11: who this computer's Claude Code is signed in as, from `claude auth status`: {logged_in, email, org, plan}."""
    if not force and _CLAUDE_ACC["v"] and time.time() - _CLAUDE_ACC["at"] < max_age:
        return _CLAUDE_ACC["v"]
    v = {"logged_in": False, "email": "", "org": "", "plan": ""}
    try:
        r = subprocess.run([shutil.which("claude") or "claude", "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30, creationflags=_NOWIN)
        d = json.loads(r.stdout or "{}")
        v = {"logged_in": bool(d.get("loggedIn")), "email": d.get("email") or "", "org": d.get("orgName") or "", "plan": d.get("subscriptionType") or ""}
    except Exception:
        pass
    _CLAUDE_ACC.update(at=time.time(), v=v)
    return v


ENGINES_DASH = {"claude": ("Claude Code", "claude"), "qwen": ("Qwen Code", "qwen")}


def _which(cmd):
    return shutil.which(cmd)


def _agent_json(name):
    try:
        return json.load(open(os.path.join(ROOT, "agent", name), encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def engine_info():
    """Owner 14.11: the agent engine this computer's bridge runs on, which engines are installed, and what happens when Claude's quota runs out."""
    e = _agent_json("engine.json").get("engine")
    fo = _agent_json("failover.json")
    return {"engine": e if e in ENGINES_DASH else "claude", "ready": {k: bool(shutil.which(c)) for k, (_, c) in ENGINES_DASH.items()},
            "labels": {k: lbl for k, (lbl, _) in ENGINES_DASH.items()},
            "failover": {"on_limit": fo.get("on_limit", True), "engine": fo.get("engine") if fo.get("engine") in ENGINES_DASH else "qwen"}}


def ai_brief():
    """Which AI this computer's bridge runs on: the provider (local / remote), the model and the Claude account (for the map and /חשבונות)."""
    pname, pmodel = active_provider()
    p = _providers().get(pname) or {}
    return {"provider": pname, "short": p.get("short") or p.get("label") or pname, "model": pmodel or p.get("model") or "",
            "where": "local" if (p.get("where") == "local" or p.get("hosts")) else "remote", "claude": claude_account_info(), "engine": engine_info()}


def claude_login_start():
    """Start `claude auth login` on this computer and return its sign-in link (kept 15 minutes); the browser then shows a code to paste back."""
    p = _LOGIN["proc"]
    if p and p.poll() is None and time.time() - _LOGIN["at"] < 900 and _LOGIN["url"]:
        return {"url": _LOGIN["url"]}
    if p and p.poll() is None:
        p.kill()
    _LOGIN.update(proc=None, url="", at=0.0)
    try:
        p = subprocess.Popen([shutil.which("claude") or "claude", "auth", "login"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", creationflags=_NOWIN)
    except Exception as e:
        return {"error": f"לא הצלחתי להפעיל את ההתחברות: {e}"}
    found = queue.Queue()

    def pump():
        for line in p.stdout:
            m = LOGIN_URL_RE.search(line)
            if m:
                found.put(m.group(0))
                return
    threading.Thread(target=pump, daemon=True).start()
    try:
        url = found.get(timeout=25)
    except queue.Empty:
        p.kill()
        return {"error": "לא התקבל קישור התחברות"}
    _LOGIN.update(proc=p, url=url, at=time.time())
    return {"url": url}


def claude_login_code(code):
    """The code the browser showed: hand it to the waiting `claude auth login`; the answer is the account now signed in."""
    code = (code or "").strip()
    p = _LOGIN["proc"]
    if not re.match(r"^\S{10,}$", code):
        return {"error": "הקוד נראה לא תקין"}
    if not p or p.poll() is not None:
        return {"error": "אין התחברות פתוחה: לחץ שוב על 'החלף חשבון'"}
    try:
        p.stdin.write(code + "\n")
        p.stdin.flush()
        p.wait(timeout=40)
    except Exception as e:
        return {"error": f"ההתחברות לא הושלמה: {e}"}
    _LOGIN["proc"] = None
    info = claude_account_info(force=True)
    return {"ok": info["logged_in"], "account": info} if info["logged_in"] else {"error": "הקוד לא התקבל. נסה שוב."}


def ai_map():
    """Owner 14.11: the map's AI lane: who thinks now, which models run on the owner's own computers (and on which) and which in the cloud,
    the OpenAI translator and the cost ladder of /אוטו."""
    allp = _providers()
    pname, pmodel = active_provider()
    out = []
    local_hosts = []
    for k, p in allp.items():
        local = p.get("where") == "local" or bool(p.get("hosts"))
        chk = provider_check_cached(k)
        out.append({"id": k, "label": p.get("short") or p.get("label") or k, "where": "local" if local else "remote", "cost": p.get("cost") or "",
                    "ready": bool(chk["ok"]), "active": k == pname, "key": (bool(_provider_key(p)) if p.get("key_file") else None),
                    "models": list(chk.get("models") or [])[:40], "model": p.get("model") or ""})
        for h in (p.get("hosts") or []):
            try:
                names = json.loads(urllib.request.urlopen(h["url"].rstrip("/") + "/api/tags", timeout=3).read().decode()).get("models", [])
                local_hosts.append({"label": h.get("label") or h["url"], "up": True, "models": len(names)})
            except Exception:
                local_hosts.append({"label": h.get("label") or h["url"], "up": False, "models": 0})
    try:
        urllib.request.urlopen("http://127.0.0.1:8799/health", timeout=1.5).read()
        proxy_up = True
    except Exception:
        proxy_up = False
    auto = allp.get("auto") or {}
    return {"active": pname, "model": pmodel or (allp.get(pname) or {}).get("model") or "", "providers": out, "local_hosts": local_hosts,
            "translator": proxy_up, "ladder": [allp[n].get("short") or n for n in (auto.get("ladder") or []) if n in allp]}


def system_map():
    """Owner 10.11/11.11: the live map of the whole system, opened from the dashboard (and as an image for WhatsApp).
    Engine = capabilities, methods and tools — kept in the bridge's code, shared by both PCs; project data = per
    conversation; data stores; outside connections; overlaps that need cleaning. Built from the current state each time."""
    ps = peers_status()
    machines = []
    for who, b in [("self", ps["self"])] + [(x["name"], x.get("brief")) for x in ps["peers"]]:
        b = b or {}
        machines.append({"name": (b.get("host_label") or b.get("machine") or who) if who == "self" else who,
                         "self": who == "self", "online": bool(b), "bridge": bool((b.get("bridge") or {}).get("connected")),
                         "bridge_version": (b.get("bridge") or {}).get("version") or "", "agent": bool(b.get("agent_alive")),
                         "dash_version": b.get("dash_version") or "", "git": ((b.get("git") or {}).get("head") or "")[:7],
                         "dirty": (b.get("git") or {}).get("dirty") or 0, "account": b.get("account") or "", "ai": b.get("ai") or {},
                         "conversations": [{"jid": g["jid"], "name": g["name"], "purpose": g.get("purpose", "")}
                                           for g in (b.get("groups") or [])]})
    tools = [{"name": os.path.basename(f), "what": TOOL_HE.get(os.path.basename(f)) or _first_doc_line(f)}
             for f in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py")))]
    data = map_data()
    for d in data:
        d["exists"] = bool(d["path"]) and os.path.exists(d["path"])
    secrets = os.path.join(ROOT, "secrets")
    try:
        ph = json.load(open(os.path.join(data[4]["path"], "_sync_state.json"), encoding="utf-8"))
    except (OSError, ValueError):
        ph = {}
    links = [{"name": "טלפון (adb דרך Tailscale)", "state": "ok" if ph.get("last_sync") else "wait", "detail": ph.get("last_sync") or ph.get("last_error") or "עוד לא סונכרן"},
             {"name": "Gmail", "state": "ok" if glob.glob(os.path.join(secrets, "gmail-*.json")) and os.path.exists(os.path.join(secrets, "gmail-client.json")) else "wait",
              "detail": "מחובר" if os.path.exists(os.path.join(secrets, "gmail-client.json")) else "ממתין לקובץ gmail-client.json"},
             {"name": "Google Sheets", "state": "ok" if os.path.exists(os.path.join(secrets, "google-sa.json")) else "wait", "detail": "חשבון שירות"},
             {"name": "TrueNAS", "state": "plan", "detail": "מתוכנן: גיבוי והעברת נתונים"}]
    overlaps = []
    for d in ("lodging_meals", "lunch", "fleet", "failover", "agent/failover"):
        if os.path.isdir(os.path.join(ROOT, d)):
            overlaps.append(f"{d}/ קיים במחשב הזה מחוץ למאגר הקוד (כלי שצריך לעבור למנוע)")
    heads = {m["git"] for m in machines if m["online"] and m["git"]}
    if len(heads) > 1:
        overlaps.append("המחשבים על גרסאות קוד שונות: " + ", ".join(sorted(heads)))
    vers = {m["bridge_version"] for m in machines if m["online"] and m["bridge_version"]}
    if len(vers) > 1:
        overlaps.append("גרסאות גשר שונות: " + ", ".join(sorted(vers)))
    return {"at": dt.datetime.now().isoformat(timespec="seconds"), "machines": machines, "tools": tools, "data": data,
            "links": links, "overlaps": overlaps, "ai": ai_map()}


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
    msgs = msgs[-80:]
    try:
        mark_received(chat, msgs, p)
    except Exception as e:
        print("receipts failed", e, file=sys.stderr)
    return msgs


def mark_received(chat, msgs, p):
    """Owner 14.11: every message of his shows that the agent got it and where it stands (in the queue / working / answered),
    so he never wonders whether it was received. Matched to its command: WhatsApp by message id, dashboard by the second it was typed."""
    cmds = rows(p["agent"], "SELECT id, msg_id, status, source FROM commands WHERE chat_jid = ? ORDER BY id DESC LIMIT 120", (chat,))
    if not cmds:
        return
    open_ids = [r["id"] for r in rows(p["agent"], "SELECT id FROM commands WHERE status IN ('pending', 'running', 'waiting_limit', 'waiting_approval') ORDER BY id")]
    by_key = {}
    for r in cmds:
        k = r["msg_id"] or ""
        if k.startswith("pc-") and k[3:].isdigit():
            k = "pc@" + dt.datetime.fromtimestamp(int(k[3:]) / 1000).isoformat(timespec="seconds").replace("T", " ")
        by_key.setdefault(k, r)
    for m in msgs:
        if m.get("tool"):
            continue
        r = by_key.get("pc@" + str(m["timestamp"])[:19]) if m.get("src") == "pc" else by_key.get(str(m["id"]))
        if r:
            m["cmd"] = {"st": r["status"], "ahead": sum(1 for i in open_ids if i < r["id"])}


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
        "version": VERSION, "build": BUILD,
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
                  "budget": c.get("daily_budget_usd", 5), "spent_today": round(spent[0]["s"], 2) if spent else 0, "usd_ils": float(c.get("usd_ils") or 3.1),
                  "heartbeat_age": None},
        "chat": group, "groups": glist, "rules": rules_of(group),
        "presence": {"pc_last": str(pc_last).replace("T", " ") if pc_last else None, "phone_last": str(phone_last)[:19] if phone_last else None},
        "model": model_info(group),
        "claude_meter": claude_meter(), "unlimited_all": bool(c.get("unlimited_all")),
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


_claude_cache = {"at": 0.0, "v": None}


def claude_meter(max_age=120):
    """Claude's plan meter (5 hours / weekly, % and reset time) for the model box. Same undocumented endpoint and sign-in
    as agent.py's claude_usage; read-only, cached; None when unavailable (never raises)."""
    if time.time() - _claude_cache["at"] < max_age:
        return _claude_cache["v"]
    v = None
    try:
        o = json.load(open(os.path.expanduser("~/.claude/.credentials.json"), encoding="utf-8")).get("claudeAiOauth") or {}
        hd = {"Authorization": "Bearer " + (o.get("accessToken") or ""), "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code"}
        u = json.loads(urllib.request.urlopen(urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers=hd), timeout=6).read().decode())
        v = {k: {"pct": (u.get(k) or {}).get("utilization"), "resets_at": (u.get(k) or {}).get("resets_at")}
             for k in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet") if (u.get(k) or {}).get("utilization") is not None}
        v["plan"] = o.get("subscriptionType") or ""
    except Exception:
        pass
    _claude_cache.update(at=time.time(), v=v)
    return v


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


# ---------------------------------------------------------------- 🤖 automations tab (owner 10.11.2026)
# Owner: "every automation, of any kind, in the bridge UI: change everything, switch everything on/off, every parameter
# editable; each automation with a name, a task tree, performance and an event log."
# Plugin automations keep their settings in the plugins' own JSON files (ppe-tracker/segment_d.json, wa_plugin.json);
# the dashboard edits only the keys a card defines (read-modify-write, a <file>.bak-ui copy before every write) and never
# sends anything, never touches send_policy.json or the company sheet. Core agent jobs: only flags config.json already has.
# Defaults below mirror the plugins' own defaults (segment_d_auto.DEFAULTS, wa_plugin .get(...) fallbacks) for display.

AU_LOCK = threading.Lock()
_au_pending = {}  # automation id -> when a change that needs an agent restart was saved (cleared on restart)


def _au_plugin_dir():
    c = cfg()
    for p in list(c.get("plugins") or []) + list(c.get("plugins_disabled") or []):
        if os.path.basename(p) in ("segment_d_auto.py", "wa_plugin.py"):
            return os.path.dirname(os.path.expanduser(p))
    return os.path.expanduser("~/ppe-tracker")


def _au_files():
    d = _au_plugin_dir()
    return {"seg": os.path.join(d, "segment_d.json"), "wa": os.path.join(d, "wa_plugin.json"),
            "cfg": os.path.join(ROOT, "agent", "config.json")}


def _ap(path, label, typ, default=None, restart=False, help="", **extra):
    """One editable parameter: dotted path inside the card's JSON file."""
    return {"path": path, "label": label, "type": typ, "default": default, "restart": restart, "help": help, **extra}


WEEKDAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]  # Python weekday(): Mon=0 .. Sun=6
SEGD_PLUGIN = {"plugin": "segment_d_auto.py", "label": "תוסף מקטע D פעיל (כל אוטומציות מקטע D)"}
_SEGD_TPL = "משתנים: {deputy_name} {deputy_role} {manager_name} {manager_role} {present} {exceptions} {lodgers} {link}"

AUTOMATIONS = [
    {"id": "lunch", "group": "מקטע D", "icon": "🍽️", "name": "הזמנת אוכל — מקטע D ← ציפורה", "file": "seg", "plugin": "segment_d_auto.py",
     "desc": "תזכורת בקבוצת המקטע, בדיקה ותיקון של תיאור הקבוצה, שליחת ההזמנה לקבוצת ציפורה ועדכון לנמען שנבחר.",
     "toggles": [{"path": "lunch.enabled", "label": "האוטומציה פעילה", "default": True},
                 {"path": "lunch.notify_after_send.approved", "label": "עדכון לנמען אחרי השליחה", "default": False}, SEGD_PLUGIN],
     "params": [
         _ap("workdays", "ימי עבודה", "weekdays", [6, 0, 1, 2, 3], help="משותף גם לתזכורות הנוכחות."),
         _ap("lunch.skip_dates", "תאריכים בלי הזמנה", "dates", [], help="תאריך בכל שורה, YYYY-MM-DD (חג, יום בלי עבודה)."),
         _ap("lunch.date_update", "עדכון התאריך בתיאור", "time", "00:05"),
         _ap("lunch.reminder.at", "תזכורת בקבוצה (א׳–ה׳ חוץ מראשון)", "time", "08:30"),
         _ap("lunch.reminder.at_sun", "תזכורת בקבוצה — יום ראשון", "time", "09:30"),
         _ap("lunch.reminder.approved_text", "נוסח התזכורת (מאושר)", "textarea", "",
             help="משתנים: {send} {meal} {pickup}. ריק = התזכורת לא נשלחת."),
         _ap("lunch.check_minutes_before", "בדיקה ותיקון התיאור — דקות לפני השליחה", "int", 5, min=1, max=60),
         _ap("lunch.send", "שליחה לציפורה", "time", "09:10"),
         _ap("lunch.send_sun", "שליחה לציפורה — יום ראשון", "time", "10:10"),
         _ap("lunch.meal", "שעת ארוחה", "time", "10:30"),
         _ap("lunch.meal_sun", "שעת ארוחה — יום ראשון", "time", "11:30"),
         _ap("lunch.pickup", "איסוף ההזמנה", "time", ""),
         _ap("lunch.pickup_sun", "איסוף ההזמנה — יום ראשון", "time", ""),
         _ap("lunch.notify_after_send.name", "נמען העדכון אחרי השליחה — שם", "text", ""),
         _ap("lunch.notify_after_send.phone", "נמען העדכון — טלפון", "phone", ""),
         _ap("lunch.notify_after_send.text", "נוסח העדכון אחרי השליחה", "textarea", "", help="משתנה: {time} (שעת השליחה)."),
     ],
     "tree": [
         "בכל 20 שניות: קורא מחדש את segment_d.json (שינוי כאן נכנס לתוקף מיד).",
         "רק בימי עבודה ולא בתאריכים החריגים; \"עצור אוכל\" בקבוצה עוצר להיום.",
         "בשעת עדכון התאריך: מחליף את התאריך בתיאור קבוצת המקטע ומאמת בקריאה חוזרת.",
         "בשעת התזכורת: שולח לקבוצת המקטע את הנוסח המאושר (פטור באישור הבעלים, הודעה אחת).",
         "דקות לפני השליחה: בודק את התיאור — תאריך, שעות, אייקונים, כתיב, מספור, לחם/מנה חסרים, נעדרים בקובץ החברה — מתקן ומדווח לך.",
         "בשעת השליחה: בדיקה אחרונה, העתקת התיאור ושליחה לקבוצת ציפורה.",
         "אחרי שליחה מוצלחת: עדכון קצר לנמען שנבחר (רק אם מאושר).",
         "עד 13:00: כל שינוי בתיאור אחרי השליחה מדווח לך (לא נשלח).",
     ],
     "log": r"\[segment_d\].*(lunch|tzipora|description edit|date update)"},

    {"id": "attendance", "group": "מקטע D", "icon": "📋", "name": "תזכורות נוכחות — סגן / מנהל אתר", "file": "seg", "plugin": "segment_d_auto.py",
     "desc": "כשסגן מנהל האתר לא עדכן היום את קובץ החברה: תזכורות אליו, ובסוף למנהל האתר. כשהסגן לא נוכח — הכול למנהל.",
     "toggles": [{"path": "attendance.enabled", "label": "האוטומציה פעילה", "default": True},
                 {"path": "attendance.approved", "label": "הנוסחים לסגן ולמנהל מאושרים", "default": False},
                 {"path": "attendance.absent_texts._approved", "label": "נוסחי \"הסגן לא נוכח\" מאושרים", "default": False}, SEGD_PLUGIN],
     "params": [
         _ap("attendance.times.deputy_1", "תזכורת ראשונה לסגן", "time", "07:45"),
         _ap("attendance.times.deputy_2", "תזכורת שנייה לסגן", "time", "12:00"),
         _ap("attendance.times.deputy_3", "תזכורת אחרונה לסגן", "time", "15:30"),
         _ap("attendance.times.manager", "פנייה למנהל האתר", "time", "16:00"),
         _ap("attendance.texts.deputy_1", "נוסח — תזכורת ראשונה לסגן", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.texts.deputy_2", "נוסח — תזכורת שנייה לסגן", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.texts.deputy_3", "נוסח — תזכורת אחרונה לסגן", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.texts.manager", "נוסח — פנייה למנהל האתר", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.absent_texts.deputy_1", "הסגן לא נוכח — נוסח ראשון למנהל", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.absent_texts.deputy_2", "הסגן לא נוכח — נוסח שני למנהל", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.absent_texts.manager", "הסגן לא נוכח — נוסח אחרון למנהל", "textarea", "", help=_SEGD_TPL),
         _ap("attendance.reply_texts.received", "תשובה — קיבלתי את העדכון", "textarea", ""),
         _ap("attendance.reply_texts.done", "תשובה — עודכן בקובץ", "textarea", "", help="משתנים: {changes} {link}"),
         _ap("attendance.reply_texts.failed", "תשובה — העדכון נכשל", "textarea", "", help="משתנים: {changes} {link}"),
         _ap("attendance.reply_texts.not_done", "תשובה — העדכון לא בוצע", "textarea", "", help="משתנים: {changes} {link}"),
         _ap("attendance.phones", "טלפון לכל תפקיד", "map", {}, help="שורה לכל תפקיד: תפקיד = טלפון"),
         _ap("attendance.deputy_role", "שם תפקיד הסגן", "text", "סגן מנהל אתר"),
         _ap("attendance.manager_role", "שם תפקיד המנהל", "text", "מנהל אתר"),
         _ap("attendance.deputy_google", "שמות הסגן בגוגל (מי שעורך את הקובץ)", "list", ["zion"]),
         _ap("attendance.manager_google", "שמות המנהל בגוגל", "list", []),
     ],
     "tree": [
         "רק בימי עבודה, כשהאוטומציה פעילה והנוסחים מאושרים.",
         "בכל שעת תזכורת: אם הסגן כבר ערך את הקובץ היום — מדלג.",
         "אם הסגן לא נוכח היום (לפי הקובץ) — הפנייה עוברת למנהל האתר בנוסחי \"הסגן לא נוכח\" (התזכורת האחרונה לסגן לא נשלחת).",
         "ממלא את הנוסח: נוכחים, חריגים, לינה וקישור לקובץ — ושולח לטלפון של התפקיד (פטור, הודעה אחת).",
         "כל תשובה של הנמען היום (טקסט או הקלטה) נקלטת: \"קיבלתי\" אליו ← סיכום ממוספר לך ← רק אחרי אישורך עדכון בקובץ ← הודעת תוצאה אליו.",
         "מדווח לך על כל תזכורת שנשלחה או שלא נשלחה.",
     ],
     "log": r"\[segment_d\].*(attendance|deputy|manager|att_|absent|reply_texts|נוכחות)"},

    {"id": "sheet", "group": "מקטע D", "icon": "👁️", "name": "מעקב קובץ החברה (קריאה בלבד)", "file": "seg", "plugin": "segment_d_auto.py",
     "desc": "בודק כל כמה דקות אם קובץ החברה השתנה: יומן עריכות (מי, מתי, מה) וגיבוי של כל גרסה.",
     "toggles": [{"path": "sheet_watch.enabled", "label": "האוטומציה פעילה", "default": True}, SEGD_PLUGIN],
     "params": [
         _ap("sheet_watch.every_minutes", "בדיקה כל (דקות)", "int", 5, min=1, max=240),
         _ap("sheet_watch.keep_days", "שמירת גיבויי גרסאות (ימים)", "int", 14, min=1, max=365),
         _ap("sheet_watch.editors", "שמות העורכים", "map", {}, help="שורה לכל עורך: שם בגוגל = שם שיוצג לך"),
         _ap("sheet_watch.local_dir", "תיקיית גיבוי במחשב הזה", "text", os.path.expanduser("~/WhatsApp-Backup/google-sheet-segment-d")),
         _ap("sheet_watch.peer_host", "המחשב השני (ssh) לעותק", "text", "ybhw"),
         _ap("sheet_watch.peer_dir", "תיקיית העותק במחשב השני", "text", os.path.expanduser("~/WhatsApp-Backup/google-sheet-segment-d").replace("\\", "/")),
     ],
     "tree": [
         "כל X דקות: קורא את זמן העריכה האחרון של הקובץ (קריאה בלבד, לעולם לא כותב).",
         "אם יש גרסה חדשה: משווה לצילום הקודם ורושם ביומן העריכות (segment_d.db) מי ערך, מתי, איזה תא ומה השתנה — בלי התראה על כל עריכה.",
         "שומר גיבוי xlsx של כל גרסה (ימי שמירה לפי ההגדרה) ומעתיק למחשב השני.",
         "רושם מי ערך היום — משמש את תזכורות הנוכחות.",
         "ב-20:00: סיכום יומי — כמה בדיקות ומי ערך.",
     ],
     "log": r"\[segment_d\].*(sheet|revision|גרסה)"},

    {"id": "report", "group": "מקטע D", "icon": "🖼️", "name": "דוח יומי גרפי — מקטע D", "file": "seg", "plugin": "segment_d_auto.py",
     "desc": "דוח תמונה (ppe-tracker/daily_report.py): טיוטה למנהל האתר לאישור, ואחר כך טיוטה לקבוצת האדמיניסטרציה. הכול כרטיסי טיוטה לאישורך.",
     "toggles": [{"path": "report.enabled", "label": "האוטומציה פעילה", "default": False}, SEGD_PLUGIN],
     "params": [
         _ap("report.manager_time", "טיוטה למנהל האתר", "time", "09:30"),
         _ap("report.group_time", "טיוטה לקבוצת האדמיניסטרציה", "time", "10:00"),
         _ap("report.manager_text", "נוסח למנהל האתר", "textarea", "", help="משתנה: {day} (היום המדווח)."),
         _ap("report.group_text", "נוסח לקבוצה", "textarea", "", help="משתנה: {day}."),
         _ap("report.group_jid", "קבוצת האדמיניסטרציה (מזהה)", "text", ""),
         _ap("report.exclude", "לא להציג בדוח", "list", [], help="שם בכל שורה."),
         _ap("report.exempt", "פטור מאישור (שליחה בלי טיוטה)", "bool", False, help="כרגע הדוח לא פטור: נשלח רק כטיוטה לאישורך."),
         _ap("report.lodging_sites.default", "אתר לינה ברירת מחדל", "text", ""),
         _ap("report.lodging_sites.sites", "אתרי לינה נוספים", "maplist", {},
             help="שורה לכל אתר: אתר = שם, שם. אם קיים segment_d_local.json עם lodging_sites — הוא גובר."),
     ],
     "tree": [
         "רק בימי עבודה, כשהאוטומציה פעילה.",
         "בשעת הטיוטה למנהל: קורא את קובץ החברה (קריאה בלבד) — נוכחות, חריגים, לינה; מחריג את השמות ברשימה ומחלק לינה לפי אתרים.",
         "בונה תמונת דוח (HTML ← PNG) של היום המדווח, ותוספת קצרה של היום אם כבר דווח.",
         "יוצר כרטיס טיוטה למנהל האתר עם התמונה (✅ שלך שולח).",
         "בשעת הקבוצה: כרטיס טיוטה לקבוצת האדמיניסטרציה עם אותה תמונה.",
         "אם הבנייה נכשלה — הודעה אליך.",
     ],
     "log": r"\[segment_d\].*report"},

    {"id": "m4_report", "group": "ציוד 4M", "icon": "☀️", "name": "דוח בוקר — ציוד ומיגון 4M", "file": "wa", "plugin": "wa_plugin.py",
     "desc": "דוח יומי אליך: חתימות שנקלטו, חליפות חסרות, מידות, דרישות פתוחות, בקשות שממתינות לאספקה וסקרים.",
     "toggles": [{"plugin": "wa_plugin.py", "label": "תוסף 4M פעיל (כל אוטומציות 4M)"}],
     "params": [
         _ap("report_time", "שעת הדוח", "time", "08:00", restart=True),
         _ap("reminder_after_days", "בקשה פתוחה מעל (ימים) — מופיעה כממתינה לאספקה", "int", 3, restart=True, min=1, max=60),
         _ap("poll_days", "סקרים מהימים האחרונים", "int", 7, restart=True, min=1, max=60),
         _ap("weekly_weekday", "יום שליחת קובץ המעקב השבועי", "weekday", 6, restart=True),
         _ap("workbook", "קובץ המעקב (xlsx)", "text", "", restart=True),
     ],
     "tree": [
         "בשעת הדוח: קורא state.json, טפסים חתומים, קישורי החתמה ודרישות פתוחות.",
         "מרכיב את הדוח: חתימות שנקלטו, חליפות חסרות, חסרה חתימה, קישור שלא חזר, מידות, דרישות פתוחות ובטיפול, מה נסגר מאתמול.",
         "מוסיף בקשות שממתינות לאספקה ממך וסקרים מהשבוע האחרון.",
         "שולח לקבוצת הבקרה (אליך בלבד).",
         "פעם בשבוע, ביום שנבחר: שולח אליך את קובץ המעקב כקובץ.",
     ],
     "log": r"\[4m\].*(report|reminders_section)|4m extra failed|4m_report"},

    {"id": "m4_extract", "group": "ציוד 4M", "icon": "🔎", "name": "חילוץ בקשות ציוד מהצ'אטים", "file": "wa", "plugin": "wa_plugin.py",
     "desc": "מזהה בקשות ציוד בהודעות ובהקלטות של העובדים ופותח/מעדכן דרישות (מודל זול, לפי מילות מפתח).",
     "toggles": [{"plugin": "wa_plugin.py", "label": "תוסף 4M פעיל (כל אוטומציות 4M)"}],
     "params": [
         _ap("extract_every_minutes", "בדיקה כל (דקות)", "int", 60, restart=True, min=5, max=1440),
         _ap("extract_model", "מודל", "choice", "haiku", restart=True, options=["haiku", "sonnet", "opus"]),
         _ap("request_keywords", "מילות מפתח", "list", [], restart=True, help="מילה או תחילית בכל שורה."),
         _ap("request_chats", "קבוצות לסריקה", "list", [], restart=True, help="מזהה קבוצה בכל שורה."),
         _ap("watch_employees", "עובדים לסריקה (צ'אט פרטי)", "map", {}, restart=True, help="שורה לכל עובד: טלפון = שם"),
     ],
     "tree": [
         "כל X דקות: אוסף הודעות והקלטות חדשות מהקבוצות ומהעובדים שברשימה.",
         "מסנן לפי מילות המפתח.",
         "מודל זול מסווג: בקשות חדשות ועדכונים לבקשות פתוחות.",
         "רושם בטבלת הדרישות (plugin.db). לא שולח כלום לאף אחד — הבקשות הן רמז, לא מקור לחוסרים.",
     ],
     "log": r"\[4m\].*extract|4m_extract"},

    {"id": "m4_procurement", "group": "ציוד 4M", "icon": "📦", "name": "תזכורת חוסרים לרכש (בר נוי)", "file": "wa", "plugin": "wa_plugin.py",
     "desc": "כל כמה ימים בשעה קבועה: הודעה אחת לרכש עם החוסרים (חליפות מ-state.json + פריטים שהוספת).",
     "toggles": [{"path": "procurement_approved", "label": "האוטומציה פעילה (נוסח מאושר)", "default": False}],
     "params": [
         _ap("procurement_time", "שעה", "time", "10:00"),
         _ap("procurement_every_days", "כל (ימים)", "int", 2, min=1, max=30),
         _ap("procurement_phone", "טלפון הרכש", "phone", ""),
         _ap("procurement_text", "נוסח ההודעה", "textarea", "", help="משתנים: {blocks} (רשימת החוסרים) {expected}"),
         _ap("procurement_with_suit", "פריטים שחסרים יחד עם חליפה", "list", [], help="פריט בכל שורה."),
         _ap("procurement_extra", "פריטים נוספים", "items", [], help="שורה לכל פריט: פריט | כמות"),
         _ap("procurement_expected.text", "הערת \"צפוי להגיע\"", "text", ""),
         _ap("procurement_expected.until", "הערת \"צפוי להגיע\" — עד תאריך", "date", ""),
         _ap("procurement_opened", "מתי נפתח כל חוסר", "map", {}, help="שורה לכל פריט: פריט = YYYY-MM-DD"),
         _ap("procurement_note", "הערות לפריטים", "map", {}, help="שורה לכל פריט: פריט = הערה"),
     ],
     "tree": [
         "כל דקה: בודק אם הגיעה השעה ואם עברו מספיק ימים מהשליחה הקודמת (שישי ושבת — עוברים ליום העבודה הבא).",
         "קורא מחדש את wa_plugin.json ו-state.json (שינוי כאן נכנס לתוקף מיד).",
         "בונה את רשימת החוסרים: חליפות + פריטים שהולכים עם חליפה + הפריטים הנוספים.",
         "אם השליחה לאחרים מושהית — לא שולח ומדווח לך.",
         "שולח הודעה אחת לרכש (פטור באישור הבעלים) ומדווח לך.",
     ],
     "log": r"\[4m\].*procurement"},

    {"id": "m4_intake", "group": "ציוד 4M", "icon": "✍️", "name": "קליטת טפסי חתימה", "file": "wa", "plugin": "wa_plugin.py",
     "desc": "טפסים חתומים מהטופס הדיגיטלי ומתמונות בווצאפ נקלטים אוטומטית ומעדכנים את קובץ המעקב.",
     "toggles": [{"plugin": "wa_plugin.py", "label": "תוסף 4M פעיל (כל אוטומציות 4M)"}],
     "params": [],
     "tree": [
         "מאזין לטופס הדיגיטלי (מוצפן) ולתמונות טופס בווצאפ (קוד QR).",
         "רושם את החתימה ב-signed.json ובונה מחדש את קובץ המעקב.",
         "מדווח לך על כל טופס שנקלט.",
     ],
     "log": r"\[4m\].*(intake|signature|form)|חתימה"},

    {"id": "m4_backup_check", "group": "ציוד 4M", "icon": "🛟", "name": "התראה כשהגיבוי לא רץ", "file": "wa", "plugin": "wa_plugin.py",
     "desc": "כל שעה: אם הגיבוי היומי לא הסתיים יותר מדי זמן — התראה אליך.",
     "toggles": [{"plugin": "wa_plugin.py", "label": "תוסף 4M פעיל (כל אוטומציות 4M)"}],
     "params": [_ap("backup_max_hours", "התראה אחרי (שעות בלי גיבוי)", "int", 26, restart=True, min=1, max=240)],
     "tree": ["כל שעה: בודק מתי הסתיים הגיבוי האחרון.", "אם עבר יותר מהמותר — התראה אחת אליך (ולא שוב על אותו גיבוי)."],
     "log": r"backup.*(alert|לא רץ)|הגיבוי היומי לא רץ"},

    # ---- core agent jobs (config.json): schedules are edited in Settings -> Schedules; here only existing on/off flags
    {"id": "core_backup", "group": "ליבת הסוכן", "icon": "💾", "name": "גיבוי לילי", "file": "cfg", "core": "backup",
     "desc": "גיבוי מסדי הנתונים והמדיה כל לילה.", "toggles": [],
     "params": [_ap("backup_time", "שעה", "time", "03:00", restart=True, readonly=True),
                _ap("backup_keep_days", "שמירת גיבויים (ימים)", "int", 14, restart=True, readonly=True)],
     "tree": ["מעתיק את messages.db ו-whatsapp.db לתיקיית הגיבוי של היום.", "משלים מדיה חסרה ומעתיק קבצים חדשים.",
              "מוחק גיבויים ישנים לפי ימי השמירה.", "גיבוי גם למחשב השני (אם מופעל)."],
     "log": r"backup"},
    {"id": "core_selftest", "group": "ליבת הסוכן", "icon": "🩺", "name": "בדיקה עצמית לילית", "file": "cfg", "core": "selftest",
     "desc": "בדיקה שקטה של כל המערכת; מדווח רק על כשל.", "toggles": [],
     "params": [_ap("selftest_time", "שעה", "time", "04:30", restart=True, readonly=True)],
     "tree": ["מריץ tools/selftest.py --quiet.", "אם משהו נכשל — הודעה אליך."], "log": r"selftest"},
    {"id": "core_morning", "group": "ליבת הסוכן", "icon": "🌅", "name": "סיכום בוקר", "file": "cfg", "core": "morning_summary",
     "desc": "סיכום קצר של פעילות הלילה בווצאפ ומי מחכה לתשובה.",
     "toggles": [{"path": "morning_summary", "label": "פעיל", "default": True, "restart": True}],
     "params": [_ap("morning_summary_time", "שעה", "time", "07:30", restart=True, readonly=True)],
     "tree": ["אוסף את ההודעות מ-20:00 אתמול.", "ריצה קצרה של מודל קל: סיכום + מי מחכה לתשובה.", "שולח לקבוצת הבקרה."],
     "log": r"morning_summary|morning summary"},
    {"id": "core_checks", "group": "ליבת הסוכן", "icon": "🔁", "name": "בדיקות יומיות (עדכונים, קבוצות, התחברות)", "file": "cfg", "core": "update_check",
     "desc": "בדיקת גרסה חדשה, שמות הקבוצות המחוברות ותוקף ההתחברות של Claude.", "toggles": [],
     "params": [_ap("update_check_time", "בדיקת עדכון", "time", "09:00", restart=True, readonly=True),
                _ap("group_check_time", "בדיקת קבוצות", "time", "10:00", restart=True, readonly=True),
                _ap("signin_check_time", "בדיקת התחברות", "time", "09:30", restart=True, readonly=True)],
     "tree": ["בדיקת עדכון: מודיע פעם אחת על כל גרסה חדשה.", "בדיקת קבוצות: משווה את שם כל קבוצה מחוברת לשם בווצאפ ומתקן.",
              "בדיקת התחברות: מזהיר 3 ימים לפני שההתחברות של Claude פגה."],
     "log": r"update_check|group_check|signin"},
    {"id": "core_day_sync", "group": "ליבת הסוכן", "icon": "🔄", "name": "סנכרון קוד יומי", "file": "cfg", "core": "day_sync",
     "desc": "שמירת הקוד של היום, סנכרון עם המאגר המשותף ובנייה מחדש כשצריך.",
     "toggles": [{"path": "day_sync", "label": "פעיל", "default": True, "restart": True}],
     "params": [_ap("day_sync_time", "שעה", "time", "23:15", restart=True, readonly=True)],
     "tree": ["שומר את קבצי הקוד (רק אם הבדיקות עוברות).", "pull + push למאגר המשותף (hub).",
              "אם הקוד השתנה — בונה את הגשר ומפעיל את הסוכן מחדש."], "log": r"day_sync"},
    {"id": "core_release", "group": "ליבת הסוכן", "icon": "🚀", "name": "שחרור גרסה לילי", "file": "cfg", "core": "auto_release",
     "desc": "פרסום השינויים החדשים כגרסה (מחשב המפתח בלבד).",
     "toggles": [{"path": "auto_release", "label": "פעיל", "default": False, "restart": True}],
     "params": [_ap("auto_release_time", "שעה", "time", "23:30", restart=True, readonly=True)],
     "tree": ["ממזג עם hub ומוודא שהקוד של כל המחשבים כלול.", "בדיקות, סריקה, תג גרסה ופרסום ל-GitHub.", "אין חדש — לא קורה כלום."],
     "log": r"auto_release|release"},
    {"id": "core_update", "group": "ליבת הסוכן", "icon": "⬇️", "name": "עדכון אוטומטי לילי", "file": "cfg", "core": "auto_update",
     "desc": "התקנת גרסה חדשה כשיש, עם בדיקה עצמית וחזרה לאחור בכשל.",
     "toggles": [{"path": "auto_update", "label": "פעיל", "default": True, "restart": True}],
     "params": [_ap("auto_update_time", "שעה", "time", "23:50", restart=True, readonly=True)],
     "tree": ["בודק אם יש גרסה חדשה.", "מעדכן: עוצר גשר וסוכן, מתקין, מפעיל, בודק — וחוזר אחורה בכשל.", "הסוכן החדש מדווח לך על התוצאה."],
     "log": r"auto_update|update\.py"},
    {"id": "core_push", "group": "ליבת הסוכן", "icon": "🔔", "name": "התראות פוש לטלפון", "file": "cfg", "core": None,
     "desc": "התראה לטלפון כשמשהו דורש את תשומת לבך עכשיו.",
     "toggles": [{"path": "push_alerts", "label": "פעיל", "default": True, "restart": True}],
     "params": [_ap("push_quiet", "שעות שקט", "text", "23:00-07:00", restart=True, readonly=True),
                _ap("push_long_minutes", "התראה על משימה ארוכה מ- (דקות)", "int", 5, restart=True, readonly=True),
                _ap("push_wait_hours", "מישהו מחכה יותר מ- (שעות)", "int", 3, restart=True, readonly=True),
                _ap("push_merge_seconds", "איחוד התראות קרובות (שניות)", "int", 20, restart=True, readonly=True)],
     "tree": ["Claude או תוסף מבקשים התראה (טקסט קצר).", "התראות קרובות מאוחדות.", "בשעות השקט נשמרות לבוקר (חוץ מתקלה דחופה)."],
     "log": r"push"},
    {"id": "core_reminders", "group": "ליבת הסוכן", "icon": "⏰", "name": "תזכורות וטיוטות מתוזמנות", "file": "cfg", "core": None,
     "desc": "כל דקה: תזכורות שהגיע זמנן אליך, וטיוטות שתוזמנו נשלחות (אישור אחד = הודעה אחת).", "toggles": [],
     "params": [], "tree": ["כל דקה: תזכורות שהגיע זמנן — לקבוצת הבקרה בלבד.", "תזכורת \"אם לא ענו\" — רק אם איש לא כתב מאז.",
                            "טיוטה מתוזמנת שעדיין ממתינה — מאושרת בגשר ונשלחת."],
     "log": r"remind|scheduled"},
]
AU_BY_ID = {x["id"]: x for x in AUTOMATIONS}


def _au_dig(obj, path):
    cur = obj
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return False, None
        cur = cur[k]
    return True, cur


def _au_plant(obj, path, v):
    keys, cur = path.split("."), obj
    for k in keys[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = v


def _au_load(fkey):
    f = _au_files()[fkey]
    try:
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def _au_fmt(typ, v):
    """Value -> what the edit box shows (lists / maps as one item per line)."""
    if typ in ("list", "dates"):
        return "\n".join(str(x) for x in (v or []))
    if typ == "map":
        return "\n".join(f"{k} = {x}" for k, x in (v or {}).items() if not str(k).startswith("_"))
    if typ == "maplist":
        return "\n".join(f"{k} = {', '.join(map(str, x or []))}" for k, x in (v or {}).items())
    if typ == "items":
        return "\n".join(f"{x.get('item', '')} | {x.get('qty', '')}" for x in (v or []) if isinstance(x, dict))
    return v


def _au_lines(v):
    return [ln.strip() for ln in (v if isinstance(v, list) else str(v or "").split("\n")) if str(ln).strip()]


def _au_sane(label, s):
    if "????" in s or "\ufffd" in s:
        raise ValueError(f"{label}: הטקסט נראה פגום (סימני שאלה במקום עברית)")
    return s


def _au_parse(p, v):
    """Edit-box value -> the JSON value the plugin expects (raises ValueError with a Hebrew message)."""
    t, lab = p["type"], p["label"]
    if t == "time":
        v = str(v or "").strip()
        if v == "" and p["default"] == "":
            return ""
        if not HHMM.match(v):
            raise ValueError(f"{lab}: שעה בפורמט HH:MM")
        return v
    if t == "date":
        v = str(v or "").strip()
        if v:
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"{lab}: תאריך YYYY-MM-DD")
        return v
    if t == "bool":
        return v is True or str(v).lower() in ("true", "1", "on")
    if t == "int":
        try:
            n = int(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"{lab}: מספר שלם")
        if not p.get("min", 0) <= n <= p.get("max", 100000):
            raise ValueError(f"{lab}: בין {p.get('min', 0)} ל-{p.get('max', 100000)}")
        return n
    if t == "weekday":
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = -1
        if not 0 <= n <= 6:
            raise ValueError(f"{lab}: יום בשבוע")
        return n
    if t == "weekdays":
        days = sorted({int(x) for x in (v if isinstance(v, list) else _au_lines(str(v).replace(",", "\n")))})
        if not days or any(not 0 <= d <= 6 for d in days):
            raise ValueError(f"{lab}: בחר לפחות יום אחד")
        return days
    if t == "choice":
        if v not in p["options"]:
            raise ValueError(f"{lab}: אחד מ-{', '.join(p['options'])}")
        return v
    if t == "phone":
        v = re.sub(r"[\s\-+()]", "", str(v or ""))
        if v and not re.fullmatch(r"\d{9,15}", v):
            raise ValueError(f"{lab}: מספר טלפון בפורמט 9725XXXXXXXX")
        return v
    if t == "text":
        v = _au_sane(lab, str(v or "").strip())
        if len(v) > 300 or "\n" in v:
            raise ValueError(f"{lab}: שורה אחת, עד 300 תווים")
        return v
    if t == "textarea":
        v = _au_sane(lab, str(v or "").replace("\r\n", "\n").strip())
        if len(v) > 5000:
            raise ValueError(f"{lab}: עד 5000 תווים")
        return v
    if t == "list":
        return [_au_sane(lab, x) for x in _au_lines(v)]
    if t == "dates":
        out = _au_lines(v)
        for x in out:
            try:
                dt.date.fromisoformat(x)
            except ValueError:
                raise ValueError(f"{lab}: \"{x}\" — תאריך YYYY-MM-DD")
        return out
    if t in ("map", "maplist"):
        out = {}
        for ln in _au_lines(v):
            if "=" not in ln:
                raise ValueError(f"{lab}: בכל שורה צריך \"=\" ({ln})")
            k, x = (s.strip() for s in ln.split("=", 1))
            if not k:
                raise ValueError(f"{lab}: חסר שם לפני \"=\" ({ln})")
            out[_au_sane(lab, k)] = [_au_sane(lab, y.strip()) for y in x.split(",") if y.strip()] if t == "maplist" else _au_sane(lab, x)
        return out
    if t == "items":
        out = []
        for ln in _au_lines(v):
            item, _, qty = (s.strip() for s in ln.partition("|"))
            if not item:
                raise ValueError(f"{lab}: חסר שם פריט ({ln})")
            out.append({"item": _au_sane(lab, item), "qty": _au_sane(lab, qty)})
        return out
    raise ValueError(f"{lab}: סוג לא מוכר")


def _au_tail(path, max_bytes=600_000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", "replace").splitlines()[1 if size > max_bytes else 0:]
    except OSError:
        return []


def _au_events():
    """actions.log + agent.log as [{at, src, text}] (oldest first)."""
    out = []
    for ln in _au_tail(os.path.join(ROOT, "agent", "actions.log")):
        m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\t(.*)$", ln)
        if m:
            out.append({"at": m.group(1), "src": "actions", "text": m.group(2)})
    for ln in _au_tail(paths()["log"]):
        m = re.match(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)$", ln)
        if m:
            out.append({"at": m.group(1), "src": "agent", "text": m.group(2)})
    out.sort(key=lambda e: e["at"])
    return out


def _au_namer():
    nm = names()

    def sub(text):  # owner rule: names, not numbers
        return re.sub(r"\b(\d{9,20})(@(?:g\.us|s\.whatsapp\.net|lid))?\b",
                      lambda m: nm.get(m.group(1)) or (m.group(0) if not m.group(2) and not m.group(1).startswith("972") else "איש קשר לא מזוהה"),
                      str(text))
    return sub


def _au_pkv(k):
    r = rows(os.path.join(_au_plugin_dir(), "plugin.db"), "SELECT v FROM kv WHERE k = ?", (k,))
    return r[0]["v"] if r else None


def _au_hm(v):
    """'2026-11-10T09:10:14' -> '10.11 09:10'."""
    m = re.match(r"^\d{4}-(\d\d)-(\d\d)[T ](\d\d:\d\d)", str(v or ""))
    return f"{m.group(2)}.{m.group(1)} {m.group(3)}" if m else (v or "—")


def _au_perf(a, eff, events, today):
    """[[label, value]] — what the automation did lately (read-only)."""
    seg = lambda k: kv("segd:" + k)
    done = lambda k: ("✓ " + _au_hm(seg(f"{k}:{today}"))) if seg(f"{k}:{today}") else "עוד לא היום"
    i = a["id"]
    if i == "lunch":
        wd = eff.get("workdays") or [6, 0, 1, 2, 3]
        work = dt.date.today().weekday() in wd and today not in (eff.get("lunch.skip_dates") or [])
        sent = seg(f"lunch_sent_desc:{today}") or ""
        return [["היום יום הזמנה", "כן" if work else "לא"],
                ["נעצר להיום (\"עצור אוכל\")", "כן" if (seg("lunch_stop") or "") == today else "לא"],
                ["עדכון תאריך", done("lunch_date")], ["תזכורת בקבוצה", done("lunch_reminder")],
                ["בדיקה ותיקון התיאור", done("lunch_check")], ["שליחה לציפורה", done("lunch_send")],
                ["מנות שנשלחו היום", str(len(re.findall(r"(?m)^\s*\d+\s*-", sent))) if sent else "—"]]
    if i == "attendance":
        names_ = {"deputy_1": "תזכורת ראשונה", "deputy_2": "תזכורת שנייה", "deputy_3": "תזכורת אחרונה", "manager": "מנהל האתר"}
        out = [[v, done("att_" + k)] for k, v in names_.items()]
        try:
            opened = json.loads(seg(f"att_open:{today}") or "{}")
        except ValueError:
            opened = {}
        out += [["ערכו את הקובץ היום", seg(f"sheet_editors:{today}") or "אף אחד"],
                ["שיחה פתוחה היום עם", opened.get("role") or "—"]]
        return out
    if i == "sheet":
        return [["בדיקות היום", seg(f"sheet_checks:{today}") or "0"], ["בדיקה אחרונה", _au_hm(seg("sheet_last_check"))],
                ["עריכה אחרונה בקובץ", _au_hm(str(seg("sheet_rev") or "").replace("Z", ""))],
                ["ערכו היום", seg(f"sheet_editors:{today}") or "אף אחד"], ["סיכום יומי (20:00)", done("sheet_daily_summary")]]
    if i == "report":
        return [["טיוטה למנהל האתר", done("report_manager")], ["טיוטה לקבוצה", done("report_group")],
                ["דוח היום נבנה", "כן" if seg(f"report_png:{today}") else "לא"]]
    if i == "m4_report":
        return [["רץ היום", "✓" if kv("day:4m_report") == today else "עוד לא היום"], ["דוח אחרון", _au_hm(_au_pkv("last_report_at"))],
                ["קובץ שבועי אחרון", _au_pkv("weekly_done") or "—"]]
    if i == "m4_extract":
        n = rows(os.path.join(_au_plugin_dir(), "plugin.db"), "SELECT count(*) n FROM requests WHERE status != 'סגור'")
        return [["ריצה אחרונה", _au_hm(kv("at:4m_extract"))], ["נסרק עד", _au_hm(_au_pkv("extract_since"))],
                ["דרישות פתוחות", str(n[0]["n"]) if n else "—"]]
    if i == "m4_procurement":
        last = _au_pkv("procurement_sent_date")
        nxt = "—"
        if last:
            try:
                d = dt.date.fromisoformat(last) + dt.timedelta(days=int(eff.get("procurement_every_days") or 2))
                while d.weekday() in (4, 5):
                    d += dt.timedelta(days=1)
                nxt = f"{d:%d.%m} {eff.get('procurement_time') or '10:00'}"
            except ValueError:
                pass
        return [["נשלח לאחרונה", last or "—"], ["השליחה הבאה (משוערת)", nxt if eff.get("_on") else "כבוי"],
                ["בדיקה אחרונה", _au_hm(kv("at:4m_procurement"))]]
    if i == "m4_intake":
        r = rows(os.path.join(_au_plugin_dir(), "plugin.db"), "SELECT count(*) n, max(at) last FROM intake WHERE ok = 1")
        rt = rows(os.path.join(_au_plugin_dir(), "plugin.db"), "SELECT count(*) n FROM intake WHERE ok = 1 AND substr(at,1,10) = ?", (today,))
        return [["טפסים שנקלטו (סה\"כ)", str(r[0]["n"]) if r else "—"], ["היום", str(rt[0]["n"]) if rt else "—"],
                ["אחרון", _au_hm(r[0]["last"]) if r else "—"]]
    if i == "m4_backup_check":
        return [["בדיקה אחרונה", _au_hm(kv("at:4m_backup_check"))], ["גיבוי אחרון הסתיים", _au_hm(kv("backup_done"))]]
    if i == "core_reminders":
        n = rows(paths()["agent"], "SELECT count(*) n FROM reminders WHERE status = 'active'")
        s = rows(paths()["agent"], "SELECT count(*) n FROM scheduled_drafts WHERE status = 'scheduled'")
        return [["תזכורות פעילות", str(n[0]["n"]) if n else "0"], ["טיוטות מתוזמנות", str(s[0]["n"]) if s else "0"],
                ["בדיקה אחרונה", re.sub(r"^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)$", r"\3.\2 \4:\5", kv("at:reminders") or "—")]]
    if a.get("core"):
        out = [["ריצה אחרונה (תאריך)", kv(f"day:{a['core']}") or "—"]]
        if a["core"] == "backup":
            out.append(["גיבוי אחרון הסתיים", _au_hm(kv("backup_done"))])
        return out
    return []


def automations_view():
    files, c, today = _au_files(), cfg(), dt.date.today().isoformat()
    docs = {k: _au_load(k) for k in files}
    events, namer = _au_events(), _au_namer()
    last_start = next((e["at"] for e in reversed(events) if e["src"] == "agent" and e["text"].startswith("agent starting")), "")
    for k, since in list(_au_pending.items()):
        if last_start and last_start >= since:
            _au_pending.pop(k, None)
    plugins_on = {os.path.basename(p) for p in c.get("plugins") or []}
    items = []
    for a in AUTOMATIONS:
        doc = docs.get(a["file"])
        toggles = []
        for idx, t in enumerate(a["toggles"]):
            if t.get("plugin"):
                toggles.append({"idx": idx, "label": t["label"], "on": t["plugin"] in plugins_on, "restart": True})
            else:
                found, v = _au_dig(doc or {}, t["path"])
                toggles.append({"idx": idx, "label": t["label"], "on": bool(v if found else t["default"]), "restart": bool(t.get("restart"))})
        params, eff = [], {}
        for p in a["params"]:
            found, v = _au_dig(doc or {}, p["path"])
            v = v if found else p["default"]
            eff[p["path"]] = v
            shown = _au_fmt(p["type"], v)
            nums = re.findall(r"\b(?:972\d{8,9}|\d{15,20}@g\.us|\d{15,20})\b", str(shown)) if p["type"] in ("phone", "list", "map", "text") else []
            hint = ", ".join(dict.fromkeys(namer(n) for n in nums)) if nums else ""
            params.append({"path": p["path"], "label": p["label"], "type": p["type"], "value": shown, "is_default": not found,
                           "restart": p["restart"], "help": p["help"], "options": p.get("options"), "min": p.get("min"), "max": p.get("max"),
                           "readonly": bool(p.get("readonly")), "names": hint})
        plugin_ok = a.get("plugin") is None or a["plugin"] in plugins_on
        main_on = (toggles[0]["on"] if toggles else True) and plugin_ok
        eff["_on"] = main_on
        rx = re.compile(a["log"], re.I)
        tag = r"\[dash\] automation " + re.escape(a["id"]) + r"\b"
        log = [dict(e, text=namer(e["text"])[:400]) for e in events if rx.search(e["text"]) or re.search(tag, e["text"])][-40:][::-1]
        try:
            perf = _au_perf(a, eff, events, today)
        except Exception as e:
            perf = [["שגיאה בקריאת הביצועים", str(e)[:120]]]
        fails = [e for e in log if re.search(r"FAILED|failed|נכשל|error|שגיאה", e["text"]) and e["at"][:10] == today]
        items.append({"id": a["id"], "group": a["group"], "icon": a["icon"], "name": a["name"], "desc": a["desc"],
                      "file": files[a["file"]], "file_ok": doc is not None, "plugin": a.get("plugin"), "plugin_on": plugin_ok,
                      "on": main_on, "toggles": toggles, "params": params, "tree": a["tree"], "perf": [[x, namer(y)] for x, y in perf],
                      "log": log, "last_event": log[0] if log else None, "fails_today": len(fails),
                      "pending_restart": a["id"] in _au_pending})
    return {"items": items, "pending_restart": [AU_BY_ID[k]["name"] for k in _au_pending], "plugin_dir": _au_plugin_dir()}


def _au_write(fkey, changes, why):
    """Read-modify-write only the changed keys of one JSON file; keep <file>.bak-ui first."""
    f = _au_files()[fkey]
    with AU_LOCK:
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        shutil.copy2(f, f + ".bak-ui")
        for path, v in changes.items():
            _au_plant(doc, path, v)
        dump_json(doc, f, ensure_ascii=False, indent=2)
    with open(os.path.join(ROOT, "agent", "actions.log"), "a", encoding="utf-8") as fh:
        fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}\t[dash] automation {why}: {os.path.basename(f)} "
                 f"{', '.join(changes)} (backup {os.path.basename(f)}.bak-ui)\n")


def automation_action(a):
    kind = a.get("do")
    if kind == "automations":
        return automations_view()
    if kind == "auto_restart":
        names_ = [AU_BY_ID[k]["name"] for k in _au_pending] or ["הגדרות אוטומציות"]
        open(os.path.join(ROOT, "agent", "restart.request"), "w", encoding="utf-8").write(
            "שינוי באוטומציות מלוח הבקרה: " + ", ".join(names_))
        return {"ok": True}
    au = AU_BY_ID.get(a.get("id"))
    if not au:
        return {"error": "אוטומציה לא מוכרת"}
    if kind == "auto_toggle":
        try:
            t = au["toggles"][int(a.get("idx", 0))]
        except (IndexError, TypeError, ValueError):
            return {"error": "מתג לא מוכר"}
        on = bool(a.get("on"))
        if t.get("plugin"):
            c = cfg()
            all_p = list(c.get("plugins") or []) + list(c.get("plugins_disabled") or [])
            full = next((p for p in all_p if os.path.basename(p) == t["plugin"]), None)
            if not full:
                return {"error": "התוסף לא מוגדר במחשב הזה"}
            c["plugins"] = [p for p in c.get("plugins") or [] if p != full] + ([full] if on else [])
            c["plugins_disabled"] = [p for p in c.get("plugins_disabled") or [] if p != full] + ([] if on else [full])
            shutil.copy2(_au_files()["cfg"], _au_files()["cfg"] + ".bak-ui")
            save_cfg(c)
            with open(os.path.join(ROOT, "agent", "actions.log"), "a", encoding="utf-8") as fh:
                fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}\t[dash] automation {au['id']} plugin {t['plugin']} -> {'on' if on else 'off'}\n")
            _au_pending[au["id"]] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return {"ok": True, "restart": True}
        if _au_load(au["file"]) is None:
            return {"error": "קובץ ההגדרות לא נמצא במחשב הזה"}
        _au_write(au["file"], {t["path"]: on}, f"{au['id']} switched {'on' if on else 'off'}")
        if t.get("restart"):
            _au_pending[au["id"]] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"ok": True, "restart": bool(t.get("restart"))}
    if kind == "auto_save":
        doc = _au_load(au["file"])
        if doc is None:
            return {"error": "קובץ ההגדרות לא נמצא במחשב הזה"}
        vals, changes, restart = a.get("values") or {}, {}, False
        try:
            for p in au["params"]:
                if p["path"] not in vals or p.get("readonly"):
                    continue
                new = _au_parse(p, vals[p["path"]])
                found, cur = _au_dig(doc, p["path"])
                old = cur if found else p["default"]
                try:  # compare in the same normal form (e.g. weekdays sorted), so an untouched card writes nothing
                    old = _au_parse(p, _au_fmt(p["type"], old))
                except (ValueError, TypeError):
                    pass
                if new != old:
                    if p["type"] == "map" and isinstance(cur, dict):  # keep the plugin's own "_status"-style keys
                        new = {**{k: x for k, x in cur.items() if str(k).startswith("_")}, **new}
                    changes[p["path"]] = new
                    restart = restart or p["restart"]
        except ValueError as e:
            return {"error": str(e)}
        if not changes:
            return {"ok": True, "changed": []}
        _au_write(au["file"], changes, f"{au['id']} edited")
        if restart:
            _au_pending[au["id"]] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"ok": True, "changed": [p["label"] for p in au["params"] if p["path"] in changes], "restart": restart}
    return {"error": "unknown action"}


def action(a):
    kind = a.get("do")
    p = paths()
    if kind in ("automations", "auto_toggle", "auto_save", "auto_restart"):
        return automation_action(a)
    if kind == "provider_set":  # legend: the bridge's provider (+ model); only a reachable one
        name = a.get("provider") or ""
        if name not in _providers():
            return {"error": "ספק לא מוכר"}
        chk = provider_check(name)
        if not chk["ok"]:
            return {"error": f"לא מחובר: {chk['detail']}"}
        mdl = a.get("model") or ""
        if mdl and chk["models"] and mdl not in chk["models"]:
            return {"error": "המודל לא נמצא אצל הספק"}
        o, now = _provider_state(), dt.datetime.now().isoformat(timespec="seconds")
        chat = a.get("chat") or ""
        if chat:  # owner 14.11: this conversation only
            if chat not in conversations():
                return {"error": "unknown conversation"}
            o.setdefault("groups", {})[chat] = {"provider": name, "model": mdl, "at": now}
        else:
            o.update({"provider": name, "model": mdl, "at": now})
        dump_json(o, _provider_file())
        _CHK[name] = (time.time(), chk)
        return {"ok": True}
    if kind == "provider_clear":  # a conversation goes back to the computer's AI
        o = _provider_state()
        (o.get("groups") or {}).pop(a.get("chat") or "", None)
        dump_json(o, _provider_file())
        return {"ok": True}
    if kind == "provider_key":  # legend: save a provider's key into secrets/ (never shown back; empty = remove)
        p = _providers().get(a.get("provider") or "")
        if not p or not p.get("key_file"):
            return {"error": "לספק הזה אין מפתח"}
        key = (a.get("key") or "").strip()
        if key and (len(key) > 400 or any(ch.isspace() for ch in key)):
            return {"error": "מפתח לא תקין"}
        os.makedirs(os.path.join(ROOT, "secrets"), exist_ok=True)
        f = os.path.join(ROOT, "secrets", p["key_file"])
        if key:
            open(f, "w", encoding="utf-8").write(key)
        elif os.path.exists(f):
            os.replace(f, f + f".removed-{dt.datetime.now():%Y%m%d%H%M%S}")  # kept aside, not deleted
        _CHK.pop(a.get("provider"), None)
        return {"ok": True, "check": provider_check(a.get("provider"))}
    if kind == "provider_add":  # legend: a new Anthropic-compatible provider (DeepSeek, ...); the key goes into secrets/ only
        name = re.sub(r"[^a-z0-9_-]", "", (a.get("id") or "").strip().lower())[:30]
        base = (a.get("base_url") or "").strip().rstrip("/")
        model = (a.get("model") or "").strip()
        key = (a.get("key") or "").strip()
        if len(name) < 2 or name in _providers():
            return {"error": "שם קצר באנגלית שעוד לא קיים (למשל deepseek)"}
        if not re.match(r"^(https://\S+|http://(127\.0\.0\.1|localhost)[:/]\S*)$", base):
            return {"error": "כתובת חייבת להתחיל ב-https://"}
        if not model or len(model) > 100:
            return {"error": "חסר שם מודל"}
        if key and (len(key) > 400 or any(ch.isspace() for ch in key)):
            return {"error": "מפתח לא תקין"}
        entry = {"label": ((a.get("label") or "").strip()[:60] or name), "kind": "anthropic", "base_url": base, "key_file": name + ".key",
                 "model": model, "how": f"ספק שהוספת מהלוח. כתובת: {base}. המפתח נשמר רק במחשב הזה."}
        if a.get("openai_format"):  # a provider that speaks only the OpenAI protocol: Claude Code talks to the local translator (tools/oai_proxy.py)
            entry["upstream"] = {"base": base}
            if "api.openai.com" in base:
                entry["upstream"]["max_tokens_field"] = "max_completion_tokens"
            entry.update(base_url=f"http://127.0.0.1:8799/p/{name}", token="local-proxy",
                         how=f"ספק בפורמט OpenAI שהוספת מהלוח ({base}), דרך שרת התרגום המקומי (פורט 8799). המפתח נשמר רק במחשב הזה.")
        models = [m.strip() for m in (a.get("models") or "").replace("\n", ",").split(",") if m.strip()][:40]
        if models:
            entry["models"] = models
        pf = os.path.join(ROOT, "agent", "providers.json")
        cur = json.load(open(pf, encoding="utf-8"))
        cur[name] = entry
        dump_json(cur, pf, ensure_ascii=False, indent=1)
        if key:
            os.makedirs(os.path.join(ROOT, "secrets"), exist_ok=True)
            open(os.path.join(ROOT, "secrets", name + ".key"), "w", encoding="utf-8").write(key)
        return {"ok": True, "id": name, "check": provider_check(name)}
    if kind == "engine_set":  # map: the agent engine of this computer
        e = a.get("engine")
        if e not in ENGINES_DASH:
            return {"error": "מנוע לא מוכר"}
        if not _which(ENGINES_DASH[e][1]):  # (a module-level helper: action() has a local `import shutil` further down)
            return {"error": f"{ENGINES_DASH[e][0]} לא מותקן במחשב הזה"}
        dump_json({"engine": e, "at": dt.datetime.now().isoformat(timespec="seconds")}, os.path.join(ROOT, "agent", "engine.json"))
        return {"ok": True, "engine": e}
    if kind == "failover_set":  # map: what happens when Claude's usage quota is used up
        cur = engine_info()["failover"]
        if a.get("on_limit") is not None:
            cur["on_limit"] = bool(a.get("on_limit"))
        if a.get("engine") in ENGINES_DASH:
            cur["engine"] = a["engine"]
        dump_json(cur, os.path.join(ROOT, "agent", "failover.json"))
        return {"ok": True, "failover": cur}
    if kind == "restart_agent":  # map: the agent restarts after the answer it is working on
        open(os.path.join(ROOT, "agent", "restart.request"), "w", encoding="utf-8").write("בקשה ממפת המערכת")
        return {"ok": True}
    if kind == "restart_bridge":  # map: the WhatsApp bridge exits, its keep-alive loop starts it again within seconds
        try:
            subprocess.run(["taskkill", "/IM", "whatsapp-bridge.exe", "/F"], capture_output=True, timeout=20, creationflags=_NOWIN)
        except Exception as e:
            return {"error": f"לא הצלחתי: {e}"}
        return {"ok": True}
    if kind == "claude_account":
        return claude_account_info(force=True)
    if kind == "claude_login_start":  # map: sign this computer in with another Claude account (link, then the code)
        return claude_login_start()
    if kind == "claude_login_code":
        return claude_login_code(a.get("code"))
    if kind == "provider_check":
        r = provider_check(a.get("provider") or "")
        _CHK[a.get("provider") or ""] = (time.time(), r)  # the chip / legend show the fresh result
        return r
    if kind == "model":
        m = a.get("model", "auto")
        set_model(a.get("chat"), m, a.get("hours") or 0)
        return {"ok": True, "model": m}
    if kind == "open_path":  # system map: open one of its data stores in Explorer (only those, nothing else)
        allowed = {d["path"] for d in map_data() if d["path"]}
        pth = a.get("path") or ""
        if pth not in allowed or not os.path.exists(pth):
            return {"error": "not a map data store"}
        subprocess.Popen(["explorer.exe", "/select," + pth if os.path.isfile(pth) else pth])
        return {"ok": True}
    if kind == "presence":  # owner 11.11: online / typing of the open chat; keeps the bridge "available" while watched
        chat = a.get("chat") or ""
        if not chat or chat == "local":
            return {"known": False}
        res = bridge("/presence_watch", {"jid": chat}, timeout=10)
        if res.get("by"):
            res["by_name"] = names().get(res["by"].split("@")[0]) or ""
        return res
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
    if kind == "delete_message":  # "delete for everyone" of one of this account's own messages (bridge /api/delete_message)
        jid, mid = a.get("jid") or "", str(a.get("id") or "")
        if jid not in conversations() or jid == LOCAL or not re.fullmatch(r"[A-Za-z0-9]{6,64}", mid):
            return {"error": "הודעה לא תקינה"}
        mine = rows(paths()["msg"], "SELECT 1 FROM messages WHERE id = ? AND chat_jid = ? AND is_from_me = 1", (mid, jid))
        if not mine:
            return {"error": "אפשר למחוק רק הודעה שנשלחה מהחשבון הזה"}
        return bridge("/delete_message", {"jid": jid, "id": mid})
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
        if path == "/legend":
            try:
                return self.send_json(legend((qs.get("chat") or [""])[0] or None))
            except Exception as e:
                return self.send_json({"error": str(e)}, 500)
        if path == "/system_map":
            try:
                return self.send_json(system_map())
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
            if rest not in ("/data", "/brief", "/legend"):  # /legend: the 🧠 chip of a conversation on the other machine (owner 14.11)
                return self.send_json({"error": "not allowed"}, 404)
            return self.send_json(peer_call(pi, rest + (("?" + q) if q else ""), timeout=30 if rest == "/legend" else 8))
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
        if self.path.split("?")[0] == "/clipboard":
            # owner 10.11: the right-click "paste" did nothing — WebView2 blocks navigator.clipboard.readText.
            # The dashboard reads this PC's clipboard for it; loopback only (never through Tailscale or a peer).
            if self.client_address[0] != "127.0.0.1" or not host.startswith("127.0.0.1:") or not origin:
                return self.send_json({"error": "forbidden"}, 403)
            return self.send_json({"text": read_clipboard()})
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
