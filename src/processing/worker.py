"""
Async worker loop: pull conversation_id from queue, load thread, pre-filter,
cache check, call Grok, persist insight. Runs in background on app startup.
"""

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.models import Conversation, Insight, Tweet
from src.db.session import get_db_context
from src.ingestion.queue import get_queue
from src.processing.batch_controller import BatchController
from src.processing.cache import get_cached_conversation_id, set_cache, thread_hash
from src.processing.grok_client import analyze_conversation
from src.processing.pre_filter import pre_filter
from src.metrics.prometheus import record_grok_error, record_grok_success

logger = logging.getLogger(__name__)

_settings = get_settings()
_batch_controller: Optional[BatchController] = None


def get_batch_controller() -> BatchController:
    global _batch_controller
    if _batch_controller is None:
        _batch_controller = BatchController()
    return _batch_controller


async def load_thread(db: AsyncSession, conversation_id: str) -> tuple[list[str], Optional[str]]:
    """Load conversation and return (ordered message texts, root_tweet_id)."""
    r = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = r.scalar_one_or_none()
    if not conv:
        return [], None
    r2 = await db.execute(
        select(Tweet).where(Tweet.conversation_id == conversation_id).order_by(Tweet.created_at)
    )
    tweets = list(r2.scalars().all())
    texts = [t.text for t in tweets]
    return texts, conv.root_tweet_id


async def process_one(conversation_id: str) -> None:
    """Load thread, pre-filter, cache check, Grok (if needed), persist insight."""
    try:
        async with get_db_context() as db:
            texts, root_id = await load_thread(db, conversation_id)
            if not texts:
                logger.warning("Empty thread for conversation_id=%s", conversation_id)
                return
            # Pre-filter
            result = pre_filter(message_count=len(texts), total_chars=sum(len(t) for t in texts))
            if not result.interesting:
                # Persist skipped reason
                existing = await db.execute(select(Insight).where(Insight.conversation_id == conversation_id))
                if existing.scalar_one_or_none() is None:
                    insight = Insight(
                        conversation_id=conversation_id,
                        grok_output={},
                        skipped_reason=result.reason,
                    )
                    db.add(insight)
                    await db.flush()
                return
            # Cache
            h = thread_hash(texts)
            cached_cid = await get_cached_conversation_id(db, h)
            if cached_cid and cached_cid != conversation_id:
                # Reuse existing insight (copy or link); for simplicity we still call Grok if no insight for this conv
                existing_insight = await db.execute(
                    select(Insight).where(Insight.conversation_id == cached_cid)
                )
                other = existing_insight.scalar_one_or_none()
                if other:
                    insight = Insight(
                        conversation_id=conversation_id,
                        grok_output=dict(other.grok_output),
                        sentiment=other.sentiment,
                        topics=other.topics,
                        gaps=other.gaps,
                        skipped_reason="cache_hit",
                    )
                    db.add(insight)
                    await db.flush()
                    return
            # Already have insight for this conversation?
            existing = await db.execute(select(Insight).where(Insight.conversation_id == conversation_id))
            if existing.scalar_one_or_none() is not None:
                await set_cache(db, h, conversation_id)
                return
            # Grok
            thread_text = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
            batch = get_batch_controller()
            await batch.acquire()
            start = time.monotonic()
            out = await analyze_conversation(thread_text)
            latency = time.monotonic() - start
            if "error" in out and out["error"]:
                batch.record_failure()
                record_grok_error()
                logger.warning("Grok error for conversation_id=%s: %s", conversation_id, out["error"])
                return
            batch.record_success(latency, out.get("total_tokens") or 0)
            record_grok_success(
                tokens=out.get("total_tokens") or 0,
                cost=out.get("cost_estimate"),
            )
            insight_data = out.get("insight") or {}
            sentiment = insight_data.get("sentiment") if isinstance(insight_data.get("sentiment"), str) else None
            topics = insight_data.get("topics") if isinstance(insight_data.get("topics"), list) else None
            gaps = insight_data.get("gaps") if isinstance(insight_data.get("gaps"), list) else None
            insight = Insight(
                conversation_id=conversation_id,
                grok_output=insight_data,
                sentiment=sentiment,
                topics=topics,
                gaps=gaps,
                prompt_tokens=out.get("prompt_tokens"),
                completion_tokens=out.get("completion_tokens"),
                cost_estimate=out.get("cost_estimate"),
            )
            db.add(insight)
            await db.flush()
            await set_cache(db, h, conversation_id)
    except Exception:
        logger.exception("process_one failed for conversation_id=%s", conversation_id)


async def worker_loop() -> None:
    """Main loop: dequeue conversation_id, process_one, repeat."""
    queue = get_queue()
    poll = _settings.WORKER_POLL_INTERVAL_SECONDS
    logger.info("Worker loop started")
    while True:
        try:
            cid = await queue.dequeue(timeout=poll)
            if cid:
                await process_one(cid)
        except asyncio.CancelledError:
            logger.info("Worker loop cancelled")
            break
        except Exception:
            logger.exception("Worker loop error")
