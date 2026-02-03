# Conversations ingest: single, bulk (array), bulk (NDJSON stream).

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    BulkConversationsIn,
    BulkIngestResponse,
    ConversationIn,
    IngestResultItem,
    IngestResponse,
)
from src.config import get_settings
from src.db.models import Conversation, Tweet
from src.ingestion.normalizer import (
    conversation_messages_to_db_messages,
    get_root_tweet_id,
)
from src.db.session import get_db_context
from src.ingestion.queue import get_queue
from src.metrics.prometheus import record_backpressure

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/conversations", tags=["conversations"])
_settings = get_settings()

MAX_STREAM_LINES = 500
STREAM_CHUNK_SIZE = 64 * 1024


def _messages_to_internal(conv: ConversationIn) -> list[dict]:
    return [
        {
            "tweet_id": m.tweet_id,
            "author_id": m.author_id,
            "text": m.text,
            "in_reply_to_id": m.in_reply_to_id,
            "quoted_id": m.quoted_id,
            "inbound": m.inbound,
            "created_at": m.created_at,
            "created_at_raw": m.created_at_raw,
        }
        for m in conv.messages
    ]


async def _upsert_conversation(
    db: AsyncSession,
    messages: list[dict],
    root_tweet_id: str,
) -> Conversation:
    """Insert or update conversation and tweets; return conversation."""
    db_messages = conversation_messages_to_db_messages(messages)
    existing = await db.execute(
        select(Conversation).where(Conversation.root_tweet_id == root_tweet_id)
    )
    conv = existing.scalar_one_or_none()
    if conv is None:
        conv = Conversation(root_tweet_id=root_tweet_id)
        db.add(conv)
        await db.flush()
    for m in db_messages:
        existing_tweet = await db.execute(select(Tweet).where(Tweet.id == m["id"]))
        if existing_tweet.scalar_one_or_none() is None:
            tweet = Tweet(
                id=m["id"],
                conversation_id=conv.id,
                author_id=m["author_id"],
                text=m["text"],
                in_reply_to_id=m.get("in_reply_to_id"),
                quoted_id=m.get("quoted_id"),
                inbound=m.get("inbound", True),
                created_at=m["created_at"],
                created_at_raw=m.get("created_at_raw"),
            )
            db.add(tweet)
    await db.flush()
    await db.refresh(conv)
    return conv


def _retry_after_seconds() -> int:
    """Retry-After header value when queue is full (backpressure)."""
    return 60


@router.post("", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def post_conversation(body: ConversationIn) -> IngestResponse:
    """
    Ingest a single conversation. Messages are normalized and stored; conversation is enqueued for analysis.
    Returns 503 with Retry-After when queue is full (backpressure).
    """
    from src.db.session import get_db_context

    queue = get_queue()
    if not queue.can_accept():
        record_backpressure()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue at capacity. Retry after the indicated time.",
            headers={"Retry-After": str(_retry_after_seconds())},
        )
    messages = _messages_to_internal(body)
    root_tweet_id = get_root_tweet_id(messages)
    if not root_tweet_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not determine root tweet id from messages.",
        )
    async with get_db_context() as db:
        conv = await _upsert_conversation(db, messages, root_tweet_id)
    enqueued = queue.enqueue(conv.id)
    if not enqueued:
        record_backpressure()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue at capacity. Retry after the indicated time.",
            headers={"Retry-After": str(_retry_after_seconds())},
        )
    return IngestResponse(
        conversation_id=conv.id,
        root_tweet_id=conv.root_tweet_id,
        message_count=len(messages),
        enqueued=True,
    )


@router.post("/bulk", response_model=BulkIngestResponse, status_code=status.HTTP_207_MULTI_STATUS)
async def post_conversations_bulk(
    body: BulkConversationsIn,
) -> BulkIngestResponse:
    """
    Ingest up to 500 conversations. Each is validated, normalized, upserted, and enqueued.
    Returns 503 with Retry-After when queue is full and cannot accept any. Otherwise 207 with backpressure for any not enqueued.
    """
    from src.db.session import get_db_context

    max_bulk = _settings.BULK_MAX_CONVERSATIONS
    if len(body.conversations) > max_bulk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {max_bulk} conversations per request.",
        )
    queue = get_queue()
    if not queue.can_accept():
        record_backpressure()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue at capacity. Retry after the indicated time.",
            headers={"Retry-After": str(_retry_after_seconds())},
        )
    results: list[IngestResultItem] = []
    accepted_list: list[tuple[str, str, int]] = []
    rejected = 0

    async with get_db_context() as db:
        for conv_in in body.conversations:
            messages = _messages_to_internal(conv_in)
            root_tweet_id = get_root_tweet_id(messages)
            if not root_tweet_id:
                rejected += 1
                results.append(
                    IngestResultItem(
                        conversation_id="",
                        root_tweet_id="",
                        message_count=len(messages),
                        enqueued=False,
                    )
                )
                continue
            try:
                conv = await _upsert_conversation(db, messages, root_tweet_id)
                accepted_list.append((conv.id, conv.root_tweet_id, len(messages)))
            except Exception:
                logger.exception("Bulk upsert failed for root_tweet_id=%s", root_tweet_id)
                rejected += 1
                results.append(
                    IngestResultItem(
                        conversation_id="",
                        root_tweet_id=root_tweet_id,
                        message_count=len(messages),
                        enqueued=False,
                    )
                )

    backpressure = False
    for conv_id, root_tweet_id, message_count in accepted_list:
        enqueued = queue.enqueue(conv_id)
        if not enqueued:
            record_backpressure()
            backpressure = True
        results.append(
            IngestResultItem(
                conversation_id=conv_id,
                root_tweet_id=root_tweet_id,
                message_count=message_count,
                enqueued=enqueued,
            )
        )

    return BulkIngestResponse(
        accepted=len(accepted_list),
        rejected=rejected,
        results=results,
        backpressure=backpressure,
    )


async def _stream_ndjson(request: Request):
    """Read request body as NDJSON (one conversation per line), upsert and enqueue, yield NDJSON results."""
    queue = get_queue()
    if not queue.can_accept():
        record_backpressure()
        yield json.dumps({"error": "queue_full", "retry_after": _retry_after_seconds()}) + "\n"
        return
    buffer = b""
    accepted = 0
    rejected = 0
    backpressure = False
    count = 0
    async for chunk in request.stream():
        if count >= MAX_STREAM_LINES:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            count += 1
            if count > MAX_STREAM_LINES:
                break
            try:
                raw = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                rejected += 1
                yield json.dumps({"error": "invalid_json", "detail": str(e)}) + "\n"
                continue
            try:
                conv_in = ConversationIn.model_validate(raw)
            except Exception as e:
                rejected += 1
                yield json.dumps({"error": "validation", "detail": str(e)}) + "\n"
                continue
            messages = _messages_to_internal(conv_in)
            root_tweet_id = get_root_tweet_id(messages)
            if not root_tweet_id:
                rejected += 1
                yield json.dumps({"error": "no_root", "message_count": len(messages)}) + "\n"
                continue
            try:
                async with get_db_context() as db:
                    conv = await _upsert_conversation(db, messages, root_tweet_id)
                enqueued = queue.enqueue(conv.id)
                if not enqueued:
                    record_backpressure()
                    backpressure = True
                accepted += 1
                yield json.dumps({
                    "conversation_id": conv.id,
                    "root_tweet_id": conv.root_tweet_id,
                    "message_count": len(messages),
                    "enqueued": enqueued,
                }) + "\n"
            except Exception as e:
                logger.exception("Stream upsert failed root_tweet_id=%s", root_tweet_id)
                rejected += 1
                yield json.dumps({"error": "upsert", "root_tweet_id": root_tweet_id, "detail": str(e)}) + "\n"
    if buffer.strip():
        count += 1
        if count <= MAX_STREAM_LINES:
            try:
                raw = json.loads(buffer.decode("utf-8"))
                conv_in = ConversationIn.model_validate(raw)
                messages = _messages_to_internal(conv_in)
                root_tweet_id = get_root_tweet_id(messages)
                if root_tweet_id:
                    async with get_db_context() as db:
                        conv = await _upsert_conversation(db, messages, root_tweet_id)
                    enqueued = queue.enqueue(conv.id)
                    if not enqueued:
                        record_backpressure()
                        backpressure = True
                    accepted += 1
                    yield json.dumps({
                        "conversation_id": conv.id,
                        "root_tweet_id": conv.root_tweet_id,
                        "message_count": len(messages),
                        "enqueued": enqueued,
                    }) + "\n"
                else:
                    rejected += 1
                    yield json.dumps({"error": "no_root"}) + "\n"
            except Exception:
                rejected += 1
                yield json.dumps({"error": "parse_or_validate"}) + "\n"
    yield json.dumps({"_summary": {"accepted": accepted, "rejected": rejected, "backpressure": backpressure}}) + "\n"


@router.post(
    "/bulk/stream",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def post_conversations_bulk_stream(request: Request):
    """Ingest conversations from NDJSON body (one JSON object per line, max 500). Streams back NDJSON results."""
    return StreamingResponse(
        _stream_ndjson(request),
        media_type="application/x-ndjson",
    )
