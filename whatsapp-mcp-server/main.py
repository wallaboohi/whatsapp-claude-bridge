from typing import List, Dict, Any, Optional
from fastmcp import FastMCP
from whatsapp import (
    search_contacts as whatsapp_search_contacts,
    list_messages as whatsapp_list_messages,
    list_chats as whatsapp_list_chats,
    get_chat as whatsapp_get_chat,
    get_direct_chat_by_contact as whatsapp_get_direct_chat_by_contact,
    get_contact_chats as whatsapp_get_contact_chats,
    get_last_interaction as whatsapp_get_last_interaction,
    get_message_context as whatsapp_get_message_context,
    send_message as whatsapp_send_message,
    send_file as whatsapp_send_file,
    send_audio_message as whatsapp_audio_voice_message,
    download_media as whatsapp_download_media
)

# Initialize FastMCP server
mcp = FastMCP("whatsapp")

@mcp.tool()
def search_contacts(query: str) -> List[Dict[str, Any]]:
    """Search WhatsApp contacts by name or phone number.
    
    Args:
        query: Search term to match against contact names or phone numbers
    """
    contacts = whatsapp_search_contacts(query)
    return contacts

@mcp.tool()
def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1
) -> List[Dict[str, Any]]:
    """Get WhatsApp messages matching specified criteria with optional context.
    
    Args:
        after: Optional ISO-8601 formatted string to only return messages after this date
        before: Optional ISO-8601 formatted string to only return messages before this date
        sender_phone_number: Optional phone number to filter messages by sender
        chat_jid: Optional chat JID to filter messages by chat
        query: Optional search term to filter messages by content
        limit: Maximum number of messages to return (default 20)
        page: Page number for pagination (default 0)
        include_context: Whether to include messages before and after matches (default True)
        context_before: Number of messages to include before each match (default 1)
        context_after: Number of messages to include after each match (default 1)
    """
    messages = whatsapp_list_messages(
        after=after,
        before=before,
        sender_phone_number=sender_phone_number,
        chat_jid=chat_jid,
        query=query,
        limit=limit,
        page=page,
        include_context=include_context,
        context_before=context_before,
        context_after=context_after
    )
    return messages

@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Dict[str, Any]]:
    """Get WhatsApp chats matching specified criteria.
    
    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by
    )
    return chats

@mcp.tool()
def get_chat(chat_jid: str, include_last_message: bool = True) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by JID.
    
    Args:
        chat_jid: The JID of the chat to retrieve
        include_last_message: Whether to include the last message (default True)
    """
    chat = whatsapp_get_chat(chat_jid, include_last_message)
    return chat

@mcp.tool()
def get_direct_chat_by_contact(sender_phone_number: str) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by sender phone number.
    
    Args:
        sender_phone_number: The phone number to search for
    """
    chat = whatsapp_get_direct_chat_by_contact(sender_phone_number)
    return chat

@mcp.tool()
def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Dict[str, Any]]:
    """Get all WhatsApp chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    chats = whatsapp_get_contact_chats(jid, limit, page)
    return chats

@mcp.tool()
def get_last_interaction(jid: str) -> str:
    """Get most recent WhatsApp message involving the contact.
    
    Args:
        jid: The JID of the contact to search for
    """
    message = whatsapp_get_last_interaction(jid)
    return message

@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message.
    
    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
    """
    context = whatsapp_get_message_context(message_id, before, after)
    return context

@mcp.tool()
def send_message(
    recipient: str,
    message: str,
    quoted_message_id: str = ""
) -> Dict[str, Any]:
    """Send a WhatsApp message to a person or group. For group chats use the JID.

    Sending to anyone except the owner normally produces a *draft* for the owner to review (bridge
    draft mode), not a sent message — tell the user the draft is waiting.

    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        message: The message text to send
        quoted_message_id: optional ID of a message in the same chat to reply to (shown quoted above the reply)
    
    Returns:
        A dictionary containing success status and a status message
    """
    # Validate input
    if not recipient:
        return {
            "success": False,
            "message": "Recipient must be provided"
        }
    
    # Call the whatsapp_send_message function with the unified recipient parameter
    success, status_message = whatsapp_send_message(recipient, message, quoted_message_id)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_file(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send a file such as a picture, raw audio, video or document via WhatsApp to the specified recipient. For group messages use the JID.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the media file to send (image, video, document)
    
    Returns:
        A dictionary containing success status and a status message
    """
    
    # Call the whatsapp_send_file function
    success, status_message = whatsapp_send_file(recipient, media_path)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def send_audio_message(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send any audio file as a WhatsApp audio message to the specified recipient. For group messages use the JID. If it errors due to ffmpeg not being installed, use send_file instead.
    
    Args:
        recipient: The recipient - either a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the audio file to send (will be converted to Opus .ogg if it's not a .ogg file)
    
    Returns:
        A dictionary containing success status and a status message
    """
    success, status_message = whatsapp_audio_voice_message(recipient, media_path)
    return {
        "success": success,
        "message": status_message
    }

@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        A dictionary containing success status, a status message, and the file path if successful
    """
    file_path = whatsapp_download_media(message_id, chat_jid)
    
    if file_path:
        return {
            "success": True,
            "message": "Media downloaded successfully",
            "file_path": file_path
        }
    else:
        return {
            "success": False,
            "message": "Failed to download media"
        }

# ---------- enhanced bridge tools ----------
import extras  # noqa: E402


@mcp.tool()
def get_status() -> Dict[str, Any]:
    """WhatsApp connection status: connected, connected_since, disconnects, sent_today, send policy (always draft for others, paused), pending drafts."""
    return extras.status()


@mcp.tool()
def get_group_info(chat_jid: str) -> Dict[str, Any]:
    """קרא מ-WhatsApp את שם הקבוצה, התיאור ומספר החברים. בשיחה הנוכחית השתמש במזהה הקבוצה שמופיע בהקשר."""
    return extras.group_info(chat_jid)


@mcp.tool()
def set_group_name(chat_jid: str, name: str) -> Dict[str, Any]:
    """שנה את שם קבוצת WhatsApp. יש להשתמש רק בעקבות בקשה מפורשת של הבעלים; נדרשת הרשאת מנהל בקבוצה."""
    return extras.set_group_name(chat_jid, name)


@mcp.tool()
def search_all(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Search text in all messages AND in transcripts of voice notes, newest first."""
    return extras.search(query, limit)


def _sms(*args: str) -> Any:
    """Owner 17.11: SMS (local copy, agent/sms.db) through tools/sms_sync.py - read-only commands only."""
    import json, os, subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(root, "tools", "sms_sync.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout)[-300:]}
    return json.loads(r.stdout)


@mcp.tool()
def sms_index(limit: int = 40) -> Dict[str, Any]:
    """SMS table of contents: one row per sender (count, unread, last time, important/spam/personal counts)."""
    return _sms("index", str(limit))


@mcp.tool()
def sms_search(query: str, limit: int = 20) -> Dict[str, Any]:
    """Full-text search in the owner's local SMS copy (fast). Returns metadata and a short snippet per SMS."""
    return _sms("search", query, "--limit", str(limit))


@mcp.tool()
def sms_show(sms_id: int) -> Dict[str, Any]:
    """Full text of one SMS from the local copy (id from sms_search / sms_index). For the owner's reading only."""
    return _sms("show", str(int(sms_id)))


def _phone(*args: str) -> Any:
    """Owner 19.11: phone-data catalog (local copy, agent/phone.db) through tools/phone_db.py - read-only commands only."""
    import json, os, subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(root, "tools", "phone_db.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout)[-300:]}
    return json.loads(r.stdout)


@mcp.tool()
def phone_who(query: str) -> Dict[str, Any]:
    """Everything known about a person: phone-contact name, WhatsApp name/push name, owner's nickname, last calls,
    WhatsApp chat activity. query = name, phone, LID or '#id' (from phone_index). Tell the owner names, not numbers."""
    return _phone("who", query)


@mcp.tool()
def phone_calls(limit: int = 30, who: Optional[str] = None) -> Dict[str, Any]:
    """The phone's call log (newest first): time, in/out/missed, duration, caller name. Optional who = name/phone/'#id'."""
    return _phone("calls", str(limit), *(["--who", who] if who else []))


@mcp.tool()
def phone_events(date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
    """Calendar events from the phone (YYYY-MM-DD; default: today to +30 days), recurring events expanded."""
    return _phone("events", *(["--from", date_from] if date_from else []), *(["--to", date_to] if date_to else []))


@mcp.tool()
def phone_search(query: str, limit: int = 20) -> Dict[str, Any]:
    """Full-text search in the phone catalog: people (all their names), contacts' emails/company, calendar events,
    call-recording transcripts."""
    return _phone("search", query, "--limit", str(limit))


@mcp.tool()
def phone_index(limit: int = 50) -> Dict[str, Any]:
    """Table of contents of people: one row per person (calls, WhatsApp messages, last contact), newest contact first."""
    return _phone("index", str(limit))


@mcp.tool()
def list_transcripts(query: Optional[str] = None, chat_jid: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Transcripts of voice notes (made locally by the agent). Optional text filter and chat filter."""
    return extras.transcripts(query, chat_jid, limit)


@mcp.tool()
def list_polls(chat_jid: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Polls with their options, each voter's choice and a tally per option."""
    return extras.polls(chat_jid, limit)


@mcp.tool()
def list_drafts(status: str = "pending", limit: int = 20) -> List[Dict[str, Any]]:
    """Drafts waiting for the owner (sends to other people become drafts in draft mode). status: pending | sent | discarded."""
    return extras.drafts(status, limit)


@mcp.tool()
def react_to_message(chat_jid: str, message_id: str, emoji: str, sender: str = "") -> Dict[str, Any]:
    """React to a message with an emoji (empty emoji removes the reaction). sender: author of that message (empty = me)."""
    return extras.react(chat_jid, message_id, emoji, sender)



if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')

# ---- Phone tools over adb (read-only; owner 17.11.2026). No input injection, no sending. ----
import subprocess as _sp  # noqa: E402
import json as _json  # noqa: E402
import os as _os  # noqa: E402


def _adb(*args: str, timeout: int = 40) -> str:
    cfg = {}
    try:
        p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "agent", "config.json")
        cfg = _json.load(open(p, encoding="utf-8")).get("phone", {})
    except Exception:
        pass
    cmd = ["adb"] + (["-s", cfg["adb"]] if cfg.get("adb") else []) + list(args)
    r = _sp.run(cmd, capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


@mcp.tool()
def phone_status() -> Dict[str, Any]:
    """Phone state: battery, charging, foreground app, screen awake, network, model/Android."""
    g = lambda c: _adb("shell", c).strip()  # noqa: E731
    bat = g("dumpsys battery")
    pick = lambda k: next((l.split(":", 1)[1].strip() for l in bat.splitlines() if l.strip().startswith(k + ":")), "")  # noqa: E731
    return {"model": g("getprop ro.product.model"), "android": g("getprop ro.build.version.release"),
            "battery": pick("level"), "ac": pick("AC powered"), "usb": pick("USB powered"),
            "foreground": g("dumpsys window | grep -m1 mCurrentFocus"),
            "awake": g("dumpsys power | grep -m1 mWakefulness")}


@mcp.tool()
def phone_screenshot() -> Dict[str, Any]:
    """Screenshot of the phone to a PNG file; returns the path (read it as an image)."""
    import time
    d = _os.path.expanduser("~/phone-data/screens"); _os.makedirs(d, exist_ok=True)
    path = _os.path.join(d, time.strftime("%Y%m%d-%H%M%S") + ".png")
    r = _sp.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, timeout=40)
    open(path, "wb").write(r.stdout)
    return {"path": path, "bytes": len(r.stdout)}


@mcp.tool()
def phone_notifications(limit: int = 30) -> List[Dict[str, str]]:
    """Current notification-shade items (app, title, text). Read-only."""
    import re
    out, cur, res = _adb("shell", "dumpsys notification --noredact", timeout=60), None, []
    for l in out.splitlines():
        m = re.search(r"NotificationRecord\(\w+: pkg=(\S+)", l)
        if m:
            cur = {"app": m.group(1), "title": "", "text": ""}; res.append(cur)
        elif cur:
            s = l.strip()
            if s.startswith("android.title=") and not cur["title"]:
                cur["title"] = s.split("(", 1)[-1].rstrip(")")
            elif s.startswith("android.text=") and not cur["text"]:
                cur["text"] = s.split("(", 1)[-1].rstrip(")")
    return [x for x in res if x["title"] or x["text"]][:limit]


@mcp.tool()
def phone_call_log(limit: int = 20) -> List[Dict[str, Any]]:
    """Recent calls (number, type 1=in 2=out 3=missed, time, duration sec). Numbers only; resolve names via search_contacts."""
    import re, datetime
    out = _adb("shell", f"content query --uri content://call_log/calls --projection number:type:date:duration --sort 'date DESC' | head -{int(limit)}")
    rows = []
    for m in re.finditer(r"number=(\S*?), type=(\d+), date=(\d+), duration=(\d+)", out):
        rows.append({"number": m.group(1), "type": int(m.group(2)),
                     "time": datetime.datetime.fromtimestamp(int(m.group(3)) / 1000).strftime("%Y-%m-%d %H:%M"),
                     "duration": int(m.group(4))})
    return rows


@mcp.tool()
def phone_apps() -> List[str]:
    """Third-party apps installed on the phone (package names)."""
    return [l[8:] for l in _adb("shell", "pm list packages -3").splitlines() if l.startswith("package:")]


@mcp.tool()
def phone_tools_index() -> Dict[str, List[str]]:
    """Table of contents of all bridge tools by area (WhatsApp, SMS, phone, data)."""
    return {
        "whatsapp": ["list_chats", "list_messages", "search_all", "get_message_context", "send_message (draft)", "send_file", "get_group_info", "react_to_message", "download_media"],
        "sms": ["sms_index", "sms_search", "sms_show", "sms_send.py (draft+approval)"],
        "phone": ["phone_status", "phone_screenshot", "phone_notifications", "phone_call_log", "phone_apps", "tools/phone_sync.py (files)",
                  "phone_who", "phone_calls (names)", "phone_events", "phone_search", "phone_index", "tools/phone_db.py (catalog)"],
        "data": ["list_transcripts", "list_polls", "list_drafts"],
    }

# Expose ASGI app for uvicorn
app = mcp.http_app()
