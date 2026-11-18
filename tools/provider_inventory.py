"""Provider / account inventory for every bridge computer (owner 18.11.2026: one registry for the 4 computers,
NOT unified - the same provider can have a different account on each computer, and that is an advantage).

Never prints or stores a secret: only names, presence, account type and a masked account label.

  python tools/provider_inventory.py local            # JSON of this computer
  python tools/provider_inventory.py all              # this computer + ssh to the others -> agent/provider_registry.json + .md
"""
import json, os, shutil, socket, subprocess, sys, glob

HOME = os.path.expanduser("~")
CLIS = ["claude", "codex", "gemini", "copilot", "opencode", "ollama", "gh", "uv", "git", "node", "adb"]
ENV_KEYS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY", "CLOUDFLARE_API_TOKEN", "GITHUB_TOKEN"]


def mask(s):
    if not s or "@" not in s:
        return s or ""
    a, b = s.split("@", 1)
    return a[:2] + "***@" + b


def jload(p):
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def local():
    out = {"host": socket.gethostname(), "user": os.path.basename(HOME), "clis": {}, "accounts": {}, "keys_in_env": [], "desktop_apps": []}
    for c in CLIS:
        p = shutil.which(c)
        if p:
            out["clis"][c] = p
    cj = jload(os.path.join(HOME, ".claude.json")) or {}
    oa = cj.get("oauthAccount") or {}
    if oa:
        out["accounts"]["claude"] = {"account": mask(oa.get("emailAddress")), "org_type": oa.get("organizationType") or oa.get("organizationRole"),
                                     "org": oa.get("organizationName") and oa["organizationName"][:3] + "***"}
    ca = jload(os.path.join(HOME, ".codex", "auth.json"))
    if ca is not None:
        out["accounts"]["codex"] = {"auth_mode": ca.get("auth_mode") or ("api_key" if ca.get("OPENAI_API_KEY") else "?"), "signed_in": True}
    ga = jload(os.path.join(HOME, ".gemini", "google_accounts.json"))
    if ga:
        out["accounts"]["gemini_cli"] = {"account": mask(ga.get("active")), "signed_in": os.path.exists(os.path.join(HOME, ".gemini", "oauth_creds.json"))}
    for rel in (".copilot/config.json", ".config/github-copilot/apps.json", "AppData/Local/github-copilot/apps.json"):
        d = jload(os.path.join(HOME, rel))
        if d:
            logged = d.get("logged_in_users") or d.get("last_logged_in_user") or list(d.keys())[:1]
            out["accounts"]["copilot"] = {"source": rel, "users": [mask(str(x.get("login") if isinstance(x, dict) else x)) for x in (logged if isinstance(logged, list) else [logged])]}
            break
    for base in (os.path.join(HOME, ".local", "share", "opencode", "auth.json"), os.path.join(HOME, "AppData", "Roaming", "opencode", "auth.json")):
        d = jload(base)
        if d:
            out["accounts"]["opencode"] = {"providers": sorted(d.keys())}
            break
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=15)
        txt = (r.stdout + r.stderr)
        out["accounts"]["github"] = {"signed_in": "Logged in" in txt, "detail": [l.strip() for l in txt.splitlines() if "account" in l.lower()][:2]}
    except Exception:
        pass
    out["keys_in_env"] = [k for k in ENV_KEYS if os.environ.get(k)]
    # desktop apps worth bridging (presence only)
    lad = os.environ.get("LOCALAPPDATA", "")
    pf = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), lad + "/Programs"]
    for name, pats in {"Claude Desktop": ["AnthropicClaude", "Claude"], "ChatGPT Desktop": ["ChatGPT"], "Copilot": ["Copilot"], "Gemini": ["Gemini"],
                       "VS Code": ["Microsoft VS Code"], "Ollama": ["Ollama"], "Chrome": ["Google/Chrome"]}.items():
        if any(p and os.path.isdir(os.path.join(p, x)) for p in pf + [lad] for x in pats):
            out["desktop_apps"].append(name)
    ks = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "secrets")
    if os.path.isdir(ks):
        names = {}
        for f in glob.glob(os.path.join(ks, "*.key*")):
            b = os.path.basename(f).split(".")[0]
            names[b] = names.get(b, 0) + 1
        out["bridge_key_files"] = names  # counts per provider, never values
    return out


def remote(host):
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    err = ""
    for runner in (["python"], ["uv", "run", "-q", "--no-project", "python"]):
        try:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host] + runner + ["-", "local"], input=src,
                               capture_output=True, text=True, timeout=90, encoding="utf-8")
            return json.loads(r.stdout[r.stdout.find("{"):])
        except Exception as e:
            err = str(e)[:200]
    return {"host": host, "error": err}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "local"
    if mode == "local":
        print(json.dumps(local(), ensure_ascii=False, indent=1))
        return
    me = socket.gethostname().lower()
    hosts = [h for h in ("ybhw", "SALON", "PERROOM")] + ([] if me in ("lap-yakir", "4-mine") else ["4-MINE"])
    reg = {"computers": [local()] + [remote(h) for h in hosts]}
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent")
    with open(os.path.join(root, "provider_registry.json"), "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    lines = ["# רישום ספקים וחשבונות לפי מחשב (נוצר אוטומטית; בלי סודות)"]
    for c in reg["computers"]:
        lines.append(f"\n## {c.get('host')}")
        if c.get("error"):
            lines.append(f"- לא נסרק: {c['error']}")
            continue
        lines.append("- כלים: " + ", ".join(c["clis"]) )
        for k, v in c["accounts"].items():
            lines.append(f"- {k}: {json.dumps(v, ensure_ascii=False)}")
        lines.append("- אפליקציות דסקטופ: " + (", ".join(c["desktop_apps"]) or "—"))
    with open(os.path.join(root, "provider_registry.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
