"""
Normalize incoming conversation payloads to internal schema.

Handles:
- API payload (MessageIn / ConversationIn) -> DB-ready structures.
- Kaggle twcs.csv row -> MessageIn-like dict for bulk load script.
"""

from datetime import datetime
from typing import Any, Optional

# Kaggle twcs.csv: tweet_id, author_id, inbound, created_at, text, response_tweet_id, in_response_to_tweet_id
# We normalize to: tweet_id, author_id, text, in_reply_to_id (from in_response_to_tweet_id), inbound, created_at
TWCS_DATE_FMT = "%a %b %d %H:%M:%S %z %Y"  # Tue Oct 31 22:10:47 +0000 2017


def parse_twcs_created_at(raw: Optional[str]) -> Optional[datetime]:
    """Parse Kaggle created_at string to datetime."""
    if not raw or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), TWCS_DATE_FMT)
    except ValueError:
        return None


def twcs_row_to_message(row: dict[str, Any]) -> dict[str, Any]:
    """
    Convert one row from twcs.csv to internal message shape.

    Expects keys: tweet_id, author_id, inbound, created_at, text, in_response_to_tweet_id.
    """
    created_at = parse_twcs_created_at(row.get("created_at"))
    inbound = str(row.get("inbound", "true")).strip().lower() in ("true", "1", "yes")
    return {
        "tweet_id": str(row.get("tweet_id", "")).strip(),
        "author_id": str(row.get("author_id", "")).strip(),
        "text": str(row.get("text", "")).strip() or "(no text)",
        "in_reply_to_id": (str(row.get("in_response_to_tweet_id", "")).strip() or None) or None,
        "inbound": inbound,
        "created_at": created_at,
        "created_at_raw": str(row.get("created_at", "")).strip() or None,
    }


def normalize_message_for_db(msg: dict[str, Any]) -> dict[str, Any]:
    """Ensure message has required fields for DB (Tweet model)."""
    created_at = msg.get("created_at")
    if created_at is None and msg.get("created_at_raw"):
        created_at = parse_twcs_created_at(msg["created_at_raw"])
    if created_at is None:
        created_at = datetime.utcnow()
    return {
        "id": msg["tweet_id"],
        "author_id": msg["author_id"],
        "text": msg["text"],
        "in_reply_to_id": msg.get("in_reply_to_id"),
        "quoted_id": msg.get("quoted_id"),
        "inbound": bool(msg.get("inbound", True)),
        "created_at": created_at,
        "created_at_raw": msg.get("created_at_raw"),
    }


def conversation_messages_to_db_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert API/Kaggle message list to DB-ready tweet dicts (with created_at)."""
    return [normalize_message_for_db(m) for m in messages]


def get_root_tweet_id(messages: list[dict[str, Any]]) -> Optional[str]:
    """
    Infer root tweet id from message list (message that is not a reply to anyone in the list).
    If multiple roots, returns the one that appears first as in_reply_to target or first message.
    """
    reply_to_ids = {m.get("in_reply_to_id") for m in messages if m.get("in_reply_to_id")}
    for m in messages:
        tid = m.get("tweet_id")
        if tid and tid not in reply_to_ids:
            return tid
    return messages[0].get("tweet_id") if messages else None
