"""Gmail for the bridge (owner 10.11): read, search and drafts in the owner's mailboxes — personal and work — through the
Gmail API. Nothing is ever sent from here: a draft is created and the owner sends it (same rule as WhatsApp drafts).

One OAuth client (Desktop app) from the owner's Google Cloud project, saved as secrets/gmail-client.json; one token per
mailbox in secrets/gmail-<account>.json (local only, never committed).

Usage (from the tool root):
  uv run -q --no-project --with google-auth-oauthlib --with google-api-python-client python -I -X utf8 tools/gmail.py <cmd>
    auth <account>                       sign in once in the browser (account = personal / work / any name)
    accounts                             mailboxes connected on this PC
    search <account> "<gmail query>" [N] e.g. "from:buyme newer_than:1y" -> id, date, from, subject
    read <account> <message_id>          headers + plain text body
    attachments <account> <message_id> <out_dir>
    draft <account> <to> <subject> <utf8 body file>   creates a draft (not sent)
"""
import base64
import email.utils
import json
import os
import sys
from email.mime.text import MIMEText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, "secrets")
CLIENT = os.path.join(SECRETS, "gmail-client.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]  # read, label, drafts; sending stays with the owner


def token_path(account):
    return os.path.join(SECRETS, f"gmail-{account}.json")


def service(account):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    p = token_path(account)
    if not os.path.exists(p):
        sys.exit(f"mailbox '{account}' is not connected on this PC: run `gmail.py auth {account}` first")
    creds = Credentials.from_authorized_user_file(p, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        open(p, "w", encoding="utf-8").write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def auth(account):
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT):
        sys.exit(f"missing {CLIENT}: download the OAuth client (Desktop app) JSON from Google Cloud and save it there")
    os.makedirs(SECRETS, exist_ok=True)
    creds = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES).run_local_server(port=0, open_browser=True)
    open(token_path(account), "w", encoding="utf-8").write(creds.to_json())
    prof = service(account).users().getProfile(userId="me").execute()
    print(json.dumps({"account": account, "email": prof.get("emailAddress"), "messages": prof.get("messagesTotal")}, ensure_ascii=False))


def header(msg, name):
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower() == name.lower()), "")


def body_text(part):
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    for p in part.get("parts", []) or []:
        t = body_text(p)
        if t:
            return t
    return ""


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = a[0]
    if cmd == "auth":
        return auth(a[1])
    if cmd == "accounts":
        names = sorted(f[6:-5] for f in os.listdir(SECRETS) if f.startswith("gmail-") and f.endswith(".json") and f != "gmail-client.json") if os.path.isdir(SECRETS) else []
        print(json.dumps({"client": os.path.exists(CLIENT), "accounts": names}, ensure_ascii=False))
        return
    svc = service(a[1])
    if cmd == "search":
        n = int(a[3]) if len(a) > 3 else 20
        ids = svc.users().messages().list(userId="me", q=a[2], maxResults=n).execute().get("messages", [])
        for m in ids:
            full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                              metadataHeaders=["From", "Subject", "Date"]).execute()
            print(json.dumps({"id": m["id"], "date": header(full, "Date"), "from": header(full, "From"),
                              "subject": header(full, "Subject")}, ensure_ascii=False))
    elif cmd == "read":
        m = svc.users().messages().get(userId="me", id=a[2], format="full").execute()
        print(f"From: {header(m, 'From')}\nTo: {header(m, 'To')}\nDate: {header(m, 'Date')}\nSubject: {header(m, 'Subject')}\n")
        print(body_text(m.get("payload", {})) or m.get("snippet", ""))
    elif cmd == "attachments":
        m = svc.users().messages().get(userId="me", id=a[2], format="full").execute()
        os.makedirs(a[3], exist_ok=True)
        stack = [m.get("payload", {})]
        while stack:
            p = stack.pop()
            stack.extend(p.get("parts", []) or [])
            aid = p.get("body", {}).get("attachmentId")
            if aid and p.get("filename"):
                data = svc.users().messages().attachments().get(userId="me", messageId=a[2], id=aid).execute()["data"]
                out = os.path.join(a[3], os.path.basename(p["filename"]))
                open(out, "wb").write(base64.urlsafe_b64decode(data))
                print(out)
    elif cmd == "draft":
        body = open(a[4], encoding="utf-8").read()
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"], msg["Subject"], msg["Date"] = a[2], a[3], email.utils.formatdate(localtime=True)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        d = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        print(json.dumps({"draft_id": d["id"], "note": "draft only — not sent"}))
    else:
        sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
