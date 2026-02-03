"""
Analysis cache: skip re-calling Grok when thread content was already analyzed.

Key = hash of normalized thread text (e.g. SHA-256 of concatenated messages).
Value = conversation_id (we reuse that conversation's insight).
"""

import hashlib
import logging
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AnalysisCache, Insight

logger = logging.getLogger(__name__)


def thread_hash(texts: list[str]) -> str:
    """SHA-256 of normalized concatenation of message texts (order matters)."""
    normalized = "\n".join((t or "").strip() for t in texts).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_cached_conversation_id(
    db: AsyncSession,
    thread_hash_value: str,
) -> Optional[str]:
    """Return conversation_id if we have a cache entry for this thread hash."""
    r = await db.execute(
        select(AnalysisCache.conversation_id).where(
            AnalysisCache.thread_hash == thread_hash_value
        )
    )
    row = r.scalar_one_or_none()
    return row


async def set_cache(
    db: AsyncSession,
    thread_hash_value: str,
    conversation_id: str,
) -> None:
    """Store cache entry: thread_hash -> conversation_id. Idempotent: ignores duplicate thread_hash."""
    stmt = pg_insert(AnalysisCache).values(
        id=str(uuid4()),
        thread_hash=thread_hash_value,
        conversation_id=conversation_id,
    ).on_conflict_do_nothing(index_elements=["thread_hash"])
    await db.execute(stmt)
    await db.flush()
