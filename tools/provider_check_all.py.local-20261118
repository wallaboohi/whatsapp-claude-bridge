"""Live checklist of AI providers on every bridge computer (owner 18.11.2026 13:06).

Not "is it installed" but "does it answer": every CLI gets a tiny ASCII prompt on the computer where it is installed and
signed in, and Ollama's local server is started if it is down. One table for all 4 computers, written to
agent/home/PROVIDERS-STATUS.md and copied to every computer (generated file, single source; out of git: account labels).
Never prints a secret: replies are cut to 80 chars, accounts are masked by provider_inventory.

  python tools/provider_check_all.py local     # JSON for this computer (used over ssh)
  python tools/provider_check_all.py all       # all 4 computers -> PROVIDERS-STATUS.md (+ .json) on every computer
"""
import json, os, shutil, socket, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

PROMPT = "Reply with exactly one word: OK"
HOME = os.path.expanduser("~")
PCS = [("yb-pc-hw", "נייח", "ybhw"), ("perroom", "חדר שינה", "PERROOM"), ("salon-pc", "סלון", "SALON"), ("lap-yakir", "לפטופ", "4-MINE")]


def _which(b):
    p = shutil.which(b)
    if b == "copilot":  # VS Code's copilot-shim only offers to install the CLI: prefer the real one
        for c in (os.path.join(os.environ.get("APPDATA", ""), "npm", "copilot.cmd"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "GitHubCopilotCLI", "copilot.exe")):
            if os.path.exists(c):
                return c
    if not p and b == "opencode":
        for c in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "OpenCode", "opencode-cli.exe"),
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "OpenCode", "opencode-cli.exe")):
            if os.path.exists(c):
                return c
    return p


def _run(args, stdin=None, timeout=150, env=None):
    t = time.time()
    try:
        r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
                           cwd=HOME, shell=False, env=env)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        ok = r.returncode == 0 and "ok" in out.lower()
        msg = out if ok else (err or out or f"exit {r.returncode}")
        return {"ok": ok, "sec": round(time.time() - t), "msg": " ".join(msg.split())[-160:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "sec": timeout, "msg": "timeout"}
    except Exception as e:
        return {"ok": False, "sec": round(time.time() - t), "msg": str(e)[:160]}


def _cmd(b):
    """npm shims on Windows are .cmd files: run them through cmd /c."""
    p = _which(b)
    if not p:
        return None
    return ["cmd", "/c", p] if p.lower().endswith((".cmd", ".bat")) else [p]


def t_claude():
    c = _cmd("claude")
    return c and _run(c + ["-p", PROMPT, "--output-format", "text"])


def t_codex():
    c = _cmd("codex")
    return c and _run(c + ["exec", "--skip-git-repo-check", "-"], stdin=PROMPT)


ROOT = "C:/Users/walla/whatsapp-mcp"  # same path on all 4 computers (also when run as "python -" over ssh)


def t_gemini():
    """Same as the bridge's gemini_cli provider: the bridge's key (secrets/gemini.key) unless the computer is signed in."""
    c = _cmd("gemini")
    if not c:
        return None
    env = dict(os.environ)
    kf = os.path.join(ROOT, "secrets", "gemini.key")
    if not os.path.exists(os.path.join(HOME, ".gemini", "oauth_creds.json")) and os.path.exists(kf):
        env["GEMINI_API_KEY"] = open(kf, encoding="utf-8").read().strip()
    return _run(c + ["-y", "--skip-trust", "-m", "gemini-flash-lite-latest", "-p", PROMPT], env=env)


def t_copilot():
    c = _cmd("copilot")
    return c and _run(c + ["-p", PROMPT, "-s", "--no-color", "--allow-all"])


def t_opencode():
    c = _cmd("opencode")
    return c and _run(c + ["run", "-m", "opencode/big-pickle", PROMPT])


def _tags(url="http://127.0.0.1:11434"):
    """Model names, smallest first."""
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=5) as r:
            return [m["name"] for m in sorted(json.load(r).get("models", []), key=lambda m: m.get("size") or 0)]
    except Exception:
        return None


def t_ollama():
    c = _cmd("ollama")
    if not c:
        return None
    started = False
    tags = _tags()
    if tags is None:  # server down: start it detached (owner: run the servers that can run)
        try:
            subprocess.Popen(c + ["serve"], creationflags=0x00000008 | 0x00000200, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, close_fds=True)
            started = True
        except Exception:
            pass
        for _ in range(15):
            time.sleep(1)
            tags = _tags()
            if tags is not None:
                break
    if tags is None:
        return {"ok": False, "sec": 0, "msg": "server not running and could not start"}
    if not tags:
        return {"ok": False, "sec": 0, "msg": "server up, no models downloaded", "models": [], "started": started}
    small = next((n for n in tags if n.startswith("bridge-qwen3-4b")), None) or next((n for n in tags if "embed" not in n), tags[0])
    t = time.time()
    try:
        body = json.dumps({"model": small, "prompt": PROMPT, "stream": False, "options": {"num_predict": 8}}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=240) as r:
            ans = json.load(r).get("response", "")
        return {"ok": bool(ans.strip()), "sec": round(time.time() - t), "msg": f"{small}: {' '.join(ans.split())[:60]}", "models": tags,
                "started": started}
    except Exception as e:
        return {"ok": False, "sec": round(time.time() - t), "msg": f"{small}: {str(e)[:100]}", "models": tags, "started": started}


def t_gh():
    c = _cmd("gh")
    if not c:
        return None
    r = subprocess.run(c + ["auth", "status"], capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
    txt = r.stdout + r.stderr
    return {"ok": "Logged in" in txt, "sec": 0, "msg": "signed in" if "Logged in" in txt else "not signed in (gh auth login)"}


TESTS = {"claude": t_claude, "codex": t_codex, "gemini": t_gemini, "copilot": t_copilot, "opencode": t_opencode, "ollama": t_ollama, "github": t_gh}


def local():
    with ThreadPoolExecutor(len(TESTS)) as ex:
        futs = {k: ex.submit(f) for k, f in TESTS.items()}
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result()
            except Exception as e:
                res[k] = {"ok": False, "sec": 0, "msg": str(e)[:160]}
    return {"host": socket.gethostname().lower(), "at": time.strftime("%Y-%m-%d %H:%M"), "tests": res}


def remote(alias):
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    err = ""
    for runner in (["python"], ["uv", "run", "-q", "--no-project", "python"]):
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias] + runner + ["-X", "utf8", "-", "local"], input=src,
                               capture_output=True, text=True, timeout=400, encoding="utf-8", errors="replace")
            return json.loads(r.stdout[r.stdout.find("{"):])
        except Exception as e:
            err = (str(e) or "no answer")[:160]
    return {"error": err}


NAMES = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "copilot": "Copilot CLI", "opencode": "OpenCode", "ollama": "Ollama (שרת מקומי)",
         "github": "GitHub (gh)"}


def main():
    if (sys.argv[1:] or ["local"])[0] == "local":
        print(json.dumps(local(), ensure_ascii=False))
        return
    me = socket.gethostname().lower()
    with ThreadPoolExecutor(4) as ex:
        futs = [(name, host, alias, ex.submit(local) if host == me else ex.submit(remote, alias)) for host, name, alias in PCS]
        rows = [(name, host, alias, f.result()) for name, host, alias, f in futs]
    reg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent", "provider_registry.json")
    accounts = {}
    try:
        for c in json.load(open(reg_path, encoding="utf-8"))["computers"]:
            accounts[(c.get("host") or "").lower()] = c.get("accounts", {})
    except Exception:
        pass
    acc_key = {"claude": "claude", "codex": "codex", "gemini": "gemini_cli", "copilot": "copilot", "opencode": "opencode"}
    L = ["# צ'ק-ליסט ספקי בינה בכל המחשבים", f"_נבדק בפועל (שאלה קצרה לכל ספק) {time.strftime('%d.%m.%Y %H:%M')}, מ-{me}. ✅ ענה · ❌ לא ענה · ➖ לא מותקן_", ""]
    L.append("| ספק | " + " | ".join(f"{n} ({h}, ssh {a})" for n, h, a, _ in rows) + " |")
    L.append("|---" * (len(rows) + 1) + "|")
    for k, title in NAMES.items():
        cells = []
        for _, host, _, r in rows:
            if r.get("error"):
                cells.append("⚠️ המחשב לא נסרק")
                continue
            t = (r.get("tests") or {}).get(k)
            if not t:
                cells.append("➖")
                continue
            acct = (accounts.get(host, {}).get(acc_key.get(k, "")) or {})
            label = acct.get("account") or acct.get("org_type") or acct.get("auth_mode") or ""
            extra = f" · {label}" if label else ""
            if k == "ollama" and t.get("models") is not None:
                extra += f" · {len(t['models'])} מודלים" + (" · הופעל עכשיו" if t.get("started") else "")
            cells.append(("✅" if t["ok"] else "❌") + f"{extra}" + ("" if t["ok"] else f" — {t['msg'][:70]}"))
        L.append(f"| {title} | " + " | ".join(cells) + " |")
    for name, host, alias, r in rows:
        if r.get("error"):
            L.append(f"\n- {name}: לא נסרק ({r['error']})")
    L.append("\n_עדכון: `uv run --no-project python -X utf8 tools/provider_check_all.py all` מכל מחשב; הקובץ מועתק לכל הארבעה._")
    text = "\n".join(L) + "\n"
    home = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent", "home")
    with open(os.path.join(home, "PROVIDERS-STATUS.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(home, "PROVIDERS-STATUS.json"), "w", encoding="utf-8") as f:
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "from": me, "computers": [{"name": n, "host": h, "ssh": a, **r} for n, h, a, r in rows]},
                  f, ensure_ascii=False, indent=1)
    for name, host, alias, r in rows:
        if host != me and not r.get("error"):
            for fn in ("PROVIDERS-STATUS.md", "PROVIDERS-STATUS.json"):
                subprocess.run(["scp", "-q", "-o", "BatchMode=yes", os.path.join(home, fn), f"{alias}:C:/Users/walla/whatsapp-mcp/agent/home/{fn}"],
                               capture_output=True, timeout=60)
    print(text)


if __name__ == "__main__":
    main()
