from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple
import os.path
import requests
import json
import audio

# Where the bridge is installed: WA_TOOL_ROOT (set by the Claude Code plugin) or the checkout this file lives in
TOOL_ROOT = os.environ.get("WA_TOOL_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WHATSAPP_API_BASE_URL = "http://127.0.0.1:8765/api"
TOKEN_FILE = os.path.join(TOOL_ROOT, "whatsapp-bridge", "store", "api_token")


def _auth():
    """Bearer token the bridge requires on every API call (created by the bridge on first start)."""
    try:
        return {"Authorization": "Bearer " + open(TOKEN_FILE).read().strip()}
    except OSError:
        return {}

@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]

def _bridge_read(kind: str, **params):
    response = requests.post(f"{WHATSAPP_API_BASE_URL}/read", json={"kind": kind, **params}, headers=_auth(), timeout=30)
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError("Bridge read failed")
    return body.get("rows", [])


def _message(row):
    return Message(datetime.fromisoformat(str(row.get("timestamp"))), row.get("sender") or "", row.get("content") or "",
                   bool(row.get("is_from_me")), row.get("chat_jid") or "", row.get("id") or "", row.get("chat_name"), row.get("media_type"))


def _chat(row):
    ts = row.get("last_message_time")
    return Chat(row.get("jid") or "", row.get("name"), datetime.fromisoformat(str(ts)) if ts else None,
                row.get("last_message"), row.get("last_sender"), row.get("last_is_from_me"))


def get_sender_name(sender_jid: str) -> str:
    rows = _bridge_read("chat", chat_jid=sender_jid)
    if rows and rows[0].get("name"):
        return rows[0]["name"]
    rows = _bridge_read("direct_chat", sender=sender_jid.split("@")[0])
    return rows[0].get("name") if rows and rows[0].get("name") else sender_jid


def format_message(message: Message, show_chat_info: bool = True) -> str:
    heading = f"[{message.timestamp:%Y-%m-%d %H:%M:%S}]"
    if show_chat_info and message.chat_name:
        heading += f" Chat: {message.chat_name}"
    prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}] " if message.media_type else ""
    sender = "Me" if message.is_from_me else get_sender_name(message.sender)
    return f"{heading} From: {sender}: {prefix}{message.content}\n"


def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> str:
    return "".join(format_message(m, show_chat_info) for m in messages) if messages else "No messages to display."


def list_messages(after=None, before=None, sender_phone_number=None, chat_jid=None, query=None, limit=20, page=0,
                  include_context=True, context_before=1, context_after=1):
    rows = _bridge_read("messages", after=after or "", before=before or "", sender=sender_phone_number or "",
                        chat_jid=chat_jid or "", query=query or "", limit=limit, page=page)
    messages = [_message(r) for r in rows]
    if include_context:
        expanded = []
        for message in messages:
            ctx = get_message_context(message.id, context_before, context_after)
            expanded.extend(ctx.before + [ctx.message] + ctx.after)
        messages = expanded
    return format_messages_list(messages)


def get_message_context(message_id: str, before: int = 5, after: int = 5) -> MessageContext:
    rows = _bridge_read("context", message_id=message_id, context_before=before, context_after=after)
    if not rows:
        raise ValueError(f"Message with ID {message_id} not found")
    messages = [_message(r) for r in rows]
    i = next((i for i, message in enumerate(messages) if message.id == message_id), 0)
    return MessageContext(messages[i], messages[:i], messages[i + 1:])


def list_chats(query=None, limit=20, page=0, include_last_message=True, sort_by="last_active"):
    return [_chat(r) for r in _bridge_read("chats", query=query or "", limit=limit, page=page, sort_by=sort_by)]


def search_contacts(query: str) -> List[Contact]:
    return [Contact((r.get("jid") or "").split("@")[0], r.get("name"), r.get("jid") or "") for r in _bridge_read("contacts", query=query)]


def get_contact_chats(jid: str, limit=20, page=0) -> List[Chat]:
    return [_chat(r) for r in _bridge_read("contact_chats", sender=jid, limit=limit, page=page)]


def get_last_interaction(jid: str) -> str:
    rows = _bridge_read("last_interaction", sender=jid, limit=1)
    return format_message(_message(rows[0])) if rows else None


def get_chat(chat_jid: str, include_last_message=True) -> Optional[Chat]:
    rows = _bridge_read("chat", chat_jid=chat_jid)
    return _chat(rows[0]) if rows else None


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    rows = _bridge_read("direct_chat", sender=sender_phone_number)
    return _chat(rows[0]) if rows else None


def send_message(recipient: str, message: str, quoted_message_id: str = "") -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "message": message,
        }
        if quoted_message_id:
            payload["quoted_id"] = quoted_message_id  # reply quoting that message
        
        response = requests.post(url, json=payload, headers=_auth())
        if response.status_code != 200 and response.headers.get("Content-Type", "").startswith("application/json"):
            result = response.json()  # drafts / pauses come back as non-200 with an explanation
            return result.get("success", False), result.get("message", response.text)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_file(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload, headers=_auth())
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        if not media_path.endswith(".ogg"):
            try:
                media_path = audio.convert_to_opus_ogg_temp(media_path)
            except Exception as e:
                return False, f"Error converting file to opus ogg. You likely need to install ffmpeg: {str(e)}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload, headers=_auth())
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    """Download media from a message and return the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        The local file path if download was successful, None otherwise
    """
    try:
        url = f"{WHATSAPP_API_BASE_URL}/download"
        payload = {
            "message_id": message_id,
            "chat_jid": chat_jid
        }
        
        response = requests.post(url, json=payload, headers=_auth())
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success", False):
                path = result.get("path")
                print(f"Media downloaded successfully: {path}")
                return path
            else:
                print(f"Download failed: {result.get('message', 'Unknown error')}")
                return None
        else:
            print(f"Error: HTTP {response.status_code} - {response.text}")
            return None
            
    except requests.RequestException as e:
        print(f"Request error: {str(e)}")
        return None
    except json.JSONDecodeError:
        print(f"Error parsing response: {response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return None
