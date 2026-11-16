"""Extra read/act functions for the MCP server, backed by the enhanced bridge and the agent's database."""
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

import requests

from whatsapp import TOOL_ROOT, WHATSAPP_API_BASE_URL, _auth, _bridge_read

ROOT = TOOL_ROOT
AGENT_DB = os.path.join(ROOT, "agent", "agent.db")


def _agent_rows(sql: str, args=()) -> List[Dict[str, Any]]:
    """Read only agent-owned transcripts; WhatsApp account data must go through the bridge API."""
    if not os.path.exists(AGENT_DB):
        return []
    con = sqlite3.connect(f"file:{AGENT_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def status() -> Dict[str, Any]:
    try:
        return requests.get(f"{WHATSAPP_API_BASE_URL}/status", headers=_auth(), timeout=10).json()
    except Exception as e:
        return {"error": str(e)}


def group_info(chat_jid: str) -> Dict[str, Any]:
    return _group_request("group_info", {"jid": chat_jid})


def set_group_name(chat_jid: str, name: str) -> Dict[str, Any]:
    chat_jid = chat_jid.strip()
    name = name.strip()
    if not chat_jid.endswith("@g.us") or not chat_jid[:-5]:
        return {"success": False, "error": "נדרש מזהה של קבוצת WhatsApp"}
    if not name or len(name) > 100:
        return {"success": False, "error": "שם הקבוצה חייב להכיל 1–100 תווים"}
    return _group_request("set_group", {"jid": chat_jid, "name": name})


def _group_request(endpoint: str, payload: Dict[str, str]) -> Dict[str, Any]:
    try:
        response = requests.post(f"{WHATSAPP_API_BASE_URL}/{endpoint}", json=payload, headers=_auth(), timeout=40)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as e:
        return {"success": False, "error": str(e)}


def transcripts(query: Optional[str], chat_jid: Optional[str], limit: int) -> List[Dict[str, Any]]:
    sql, args = "SELECT id, chat_jid, sender, ts, text FROM transcripts WHERE 1=1", []
    if query:
        sql += " AND text LIKE ?"
        args.append(f"%{query}%")
    if chat_jid:
        sql += " AND chat_jid = ?"
        args.append(chat_jid)
    return _agent_rows(sql + " ORDER BY ts DESC LIMIT ?", args + [limit])


def polls(chat_jid: Optional[str], limit: int) -> List[Dict[str, Any]]:
    out = _bridge_read("polls", chat_jid=chat_jid or "", limit=limit)
    for p in out:
        p["options"] = json.loads(p["options"] or "[]")
        votes = _bridge_read("poll_votes", message_id=p["poll_id"], chat_jid=p["chat_jid"])
        p["votes"] = [{"voter": v["voter"], "chose": json.loads(v["options"] or "[]"), "at": v["timestamp"]} for v in votes]
        p["tally"] = {o: sum(o in v["chose"] for v in p["votes"]) for o in p["options"]}
    return out


def drafts(status_: str, limit: int) -> List[Dict[str, Any]]:
    return _bridge_read("drafts", status=status_, limit=limit)


def react(chat_jid: str, message_id: str, emoji: str, sender: str = "") -> Dict[str, Any]:
    try:
        return requests.post(f"{WHATSAPP_API_BASE_URL}/react", json={"chat_jid": chat_jid, "message_id": message_id,
                             "sender": sender, "emoji": emoji}, headers=_auth(), timeout=30).json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def search(query: str, limit: int) -> List[Dict[str, Any]]:
    """Text search over messages and voice transcripts together."""
    msgs = _bridge_read("search", query=query, limit=limit)
    for m in msgs:
        m["kind"] = "message"
    tr = transcripts(query, None, limit)
    for t in tr:
        t["kind"] = "voice transcript"
        t["timestamp"], t["content"] = t.pop("ts"), t.pop("text")
    return sorted(msgs + tr, key=lambda r: str(r.get("timestamp")), reverse=True)[:limit]
