"""Re-post the approval card of an existing pending draft (full text + attachment) in its group:  wa_draft_card.py 34
Does not create a draft and cannot approve or send it; a ✅ on the new card approves the same draft."""
import json, os, sys, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
token = open(os.path.join(ROOT, "whatsapp-bridge", "store", "api_token")).read().strip()


def repost(i):
    body = json.dumps({"id": i}).encode()
    req = urllib.request.Request("http://127.0.0.1:8765/api/draft_repost", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")
    except Exception as e:
        return str(e)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for a in sys.argv[1:]:
        print(a, repost(int(a)).strip()[:160])
