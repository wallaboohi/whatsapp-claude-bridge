"""Load a chat's older history from the owner's phone, page after page, until it is complete (owner 10.11).

WhatsApp answers an on-demand history request with one batch (up to ~500 messages older than the oldest stored one)
a few seconds later; asking again continues from the new oldest message. This keeps asking until the oldest message
stops moving (the start of the chat) or is older than --until.

Usage: uv run --no-project python tools/wa_history.py <chat_jid> [--until 2024-01-01] [--max-pages 40]
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "whatsapp-bridge", "store")


def oldest(chat):
    with sqlite3.connect(f"file:{os.path.join(STORE, 'messages.db')}?mode=ro", uri=True, timeout=30) as db:
        n, ts = db.execute("SELECT COUNT(*), MIN(timestamp) FROM messages WHERE chat_jid = ?", (chat,)).fetchone()
    return n, str(ts or "")


def ask(chat, count=500):
    tok = open(os.path.join(STORE, "api_token")).read().strip()
    req = urllib.request.Request("http://127.0.0.1:8765/api/history_sync", json.dumps({"jid": chat, "count": count}).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
    except urllib.error.HTTPError as e:
        return {"success": False, "message": e.read().decode("utf-8", "replace")[:200]}


def main():
    a = argparse.ArgumentParser()
    a.add_argument("chat")
    a.add_argument("--until", default="")
    a.add_argument("--max-pages", type=int, default=40)
    a.add_argument("--wait", type=int, default=25, help="seconds to wait for each batch")
    o = a.parse_args()
    n0, first = oldest(o.chat)
    print(f"start: {n0} messages, oldest {first[:16] or '-'}")
    still = 0
    for page in range(1, o.max_pages + 1):
        r = ask(o.chat)
        if not r.get("success"):
            sys.exit(f"history request failed: {r.get('message')}")
        before = oldest(o.chat)
        for _ in range(o.wait):  # the batch arrives asynchronously
            time.sleep(1)
            if oldest(o.chat) != before:
                time.sleep(3)
                break
        n, ts = oldest(o.chat)
        print(f"page {page}: {n} messages, oldest {ts[:16]}")
        if (n, ts) == before:
            still += 1
            if still >= 2:  # asked twice with nothing new: the start of the chat (or the phone has no more)
                break
        else:
            still = 0
        if o.until and ts and ts[:10] <= o.until:
            break
    n, ts = oldest(o.chat)
    print(f"done: {n} messages (+{n - n0}), oldest {ts[:16]}")


if __name__ == "__main__":
    main()
