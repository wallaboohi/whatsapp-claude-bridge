"""The bridge's browser and web search, for every AI the bridge hosts (owner 21.11 20:00).

The browser: Playwright driving the Chrome installed on this computer, in a profile of the bridge's own (agent/browser_profile, never
the owner's personal profile), without a window. One thread owns it; every call goes through that thread (Playwright's sync API
must stay on the thread that started it).

Web search never gets stuck — three tools in a row (owner: "3 tools so it never gets stuck"):
  1. LangSearch API (secrets/langsearch.key)   2. Tavily API (secrets/tavily.key)   3. Jina API (secrets/jina.key)
The browser opens, reads, clicks and types on pages; it is not used to search (21.11: wrong results for Hebrew).
A site that shows a robot check is not worked around: the next tool answers.
"""
import base64
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "agent", "browser_profile")
SHOTS = os.path.join(ROOT, "agent", "outbox")
TEXT_CAP = 20000
_q = queue.Queue()
_worker = {"thread": None}


def _key(name):
    for d in (os.path.join(ROOT, "secrets"), os.path.join(ROOT, "SECRETS")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            k = open(p, encoding="utf-8").read().strip()
            if k:
                return k
    return ""


# ---------------------------------------------------------------- the browser thread

def _run_worker():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    ctx = None
    while True:
        fn, args, box, done = _q.get()
        try:
            if ctx is None:
                os.makedirs(PROFILE, exist_ok=True)
                ctx = pw.chromium.launch_persistent_context(PROFILE, channel="chrome", headless=True, locale="he-IL")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            box["r"] = fn(page, *args)
        except Exception as e:
            box["e"] = e
            if "closed" in str(e).lower() or "target" in str(e).lower():
                try:
                    ctx.close()
                except Exception:
                    pass
                ctx = None  # the next call starts a fresh browser
        done.set()


def _call(fn, *args, timeout=90):
    if not _worker["thread"] or not _worker["thread"].is_alive():
        _worker["thread"] = threading.Thread(target=_run_worker, daemon=True, name="bridge-browser")
        _worker["thread"].start()
    box, done = {}, threading.Event()
    _q.put((fn, args, box, done))
    if not done.wait(timeout):
        raise TimeoutError(f"the browser did not answer in {timeout} s")
    if "e" in box:
        raise box["e"]
    return box["r"]


def _text(page):
    return re.sub(r"\n\s*\n+", "\n", page.inner_text("body") or "").strip()[:TEXT_CAP]


def _open(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    return {"url": page.url, "title": page.title(), "text": _text(page)}


def bing_target(href):
    """Bing's own redirect (bing.com/ck/a?...&u=a1<base64>) -> the real address."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href or "").query)
    u = (q.get("u") or [""])[0]
    if "bing.com/ck/a" in (href or "") and u.startswith("a1"):
        b = u[2:]
        try:
            return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "replace")
        except Exception:
            return href
    return href


def _bing(page, query, n):
    page.goto("https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "adlt": "strict", "mkt": "he-IL"}), wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector("li.b_algo", timeout=8000)
    except Exception:
        pass
    rows = page.eval_on_selector_all("li.b_algo", """els => els.map(e => {
        const a = e.querySelector('h2 a'); const p = e.querySelector('.b_caption p, p');
        return a ? [a.innerText, a.href, p ? p.innerText : ''] : null }).filter(x => x)""")
    if not rows:
        body = (page.content() or "").lower()
        raise RuntimeError("robot check" if ("captcha" in body or "challenge" in body) else "no results")
    return [{"title": t.strip(), "url": bing_target(h), "snippet": (s or "").strip()} for t, h, s in rows[:n]]


def _click(page, target):
    try:
        page.get_by_text(target, exact=False).first.click(timeout=8000)
    except Exception:
        page.click(target, timeout=8000)
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    return {"url": page.url, "title": page.title()}


def _type(page, selector, text, submit):
    page.fill(selector, text, timeout=8000)
    if submit:
        page.press(selector, "Enter")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    return {"url": page.url, "title": page.title()}


def _shot(page):
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, f"browser_{time.strftime('%Y%m%d-%H%M%S')}.png")
    page.screenshot(path=path)
    return path


def browser_open(url):
    return _call(_open, url)


def browser_read():
    return _call(lambda page: {"url": page.url, "title": page.title(), "text": _text(page)})


def browser_click(target):
    return _call(_click, target)


def browser_type(selector, text, submit=False):
    return _call(_type, selector, text, bool(submit))


def browser_screenshot():
    return _call(_shot)


# ---------------------------------------------------------------- web search: three tools in a row

def _post_json(url, body, key, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def search_langsearch(query, n):
    k = _key("langsearch.key")
    if not k:
        raise RuntimeError("no key (secrets/langsearch.key)")
    d = _post_json("https://api.langsearch.com/v1/web-search", {"query": query, "count": n, "summary": True}, k)
    rows = (((d.get("data") or {}).get("webPages") or {}).get("value")) or []
    return [{"title": r.get("name", ""), "url": r.get("url", ""), "snippet": r.get("summary") or r.get("snippet") or ""} for r in rows[:n]]


def search_tavily(query, n):
    k = _key("tavily.key")
    if not k:
        raise RuntimeError("no key (secrets/tavily.key)")
    d = _post_json("https://api.tavily.com/search", {"query": query, "max_results": n}, k)
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in (d.get("results") or [])[:n]]


def search_browser(query, n):
    return _call(_bing, query, n)


def search_jina(query, n):
    k = _key("jina.key")
    if not k:
        raise RuntimeError("no key (secrets/jina.key)")
    req = urllib.request.Request("https://s.jina.ai/?" + urllib.parse.urlencode({"q": query}),
                                 headers={"Authorization": "Bearer " + k, "Accept": "application/json", "X-Respond-With": "no-content"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description") or (r.get("content") or "")[:300]}
            for r in (d.get("data") or [])[:n]]


# owner 21.11 20:20: Bing in a browser run by code gave unrelated results for Hebrew (Microsoft account pages, once adult content)
# while English was fine: a wrong answer is worse than none, so web search is three search APIs in a row; the browser is for pages.
ENGINES = (("LangSearch", search_langsearch), ("Tavily", search_tavily), ("Jina", search_jina))


def web_search(query, max_results=8):
    n = max(1, min(int(max_results or 8), 20))
    tried = []
    for name, fn in ENGINES:
        try:
            rows = fn(query, n)
        except Exception as e:
            tried.append(f"{name}: {str(e)[:80]}")
            continue
        if rows:
            return {"engine": name, "results": rows}
        tried.append(f"{name}: no results")
    return {"engine": None, "results": [], "tried": tried}
