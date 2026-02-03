"""
GET /api/v1/trends - time-windowed aggregates (volume, sentiment drift, top gaps/topics).

Query params: window (e.g. 1d, 7d).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import (
    TrendSentimentPoint,
    TrendVolumePoint,
    TrendsResponse,
)
from src.db.models import Insight
from src.db.session import get_db

router = APIRouter(prefix="/trends", tags=["trends"])


def _parse_window(window: str) -> timedelta:
    """Parse window string (e.g. 1d, 7d) to timedelta."""
    w = (window or "7d").strip().lower()
    if w.endswith("d"):
        days = int(w[:-1] or 1)
        return timedelta(days=days)
    if w.endswith("h"):
        hours = int(w[:-1] or 1)
        return timedelta(hours=hours)
    return timedelta(days=7)


@router.get("", response_model=TrendsResponse)
async def get_trends(
    db: AsyncSession = Depends(get_db),
    window: str = Query("7d", description="Time window: e.g. 1d, 7d"),
) -> TrendsResponse:
    """
    Time-windowed aggregates: volume over time, sentiment drift, top gaps, top topics.
    """
    delta = _parse_window(window)
    now = datetime.now(timezone.utc)
    since = now - delta
    # Volume by day
    bucket_col = func.date_trunc("day", Insight.created_at)
    vol_q = (
        select(
            bucket_col.label("bucket"),
            func.count(Insight.id).label("count"),
        )
        .where(Insight.created_at >= since)
        .where(Insight.skipped_reason.is_(None))
        .group_by(bucket_col)
        .order_by(bucket_col)
    )
    vol_r = await db.execute(vol_q)
    volume = [
        TrendVolumePoint(
            bucket=str(row[0].isoformat() if hasattr(row[0], "isoformat") else row[0]),
            count=row[1],
        )
        for row in vol_r.all()
    ]
    # Sentiment by day
    bucket_col2 = func.date_trunc("day", Insight.created_at)
    sent_q = (
        select(
            bucket_col2.label("bucket"),
            Insight.sentiment,
            func.count(Insight.id).label("cnt"),
        )
        .where(Insight.created_at >= since)
        .where(Insight.skipped_reason.is_(None))
        .group_by(bucket_col2, Insight.sentiment)
    )
    sent_r = await db.execute(sent_q)
    by_bucket: dict[str, dict[str, int]] = {}
    for row in sent_r.all():
        b = str(row[0].isoformat() if hasattr(row[0], "isoformat") else row[0])
        if b not in by_bucket:
            by_bucket[b] = {"positive": 0, "negative": 0, "neutral": 0, "other": 0}
        s = (row[1] or "").lower()
        cnt = row[2]
        if s == "positive":
            by_bucket[b]["positive"] += cnt
        elif s == "negative":
            by_bucket[b]["negative"] += cnt
        elif s == "neutral":
            by_bucket[b]["neutral"] += cnt
        else:
            by_bucket[b]["other"] += cnt
    sentiment_drift = [
        TrendSentimentPoint(
            bucket=b,
            positive=data["positive"],
            negative=data["negative"],
            neutral=data["neutral"],
            other=data["other"],
        )
        for b, data in sorted(by_bucket.items())
    ]
    # Top gaps (flatten gaps JSONB array and count)
    gaps_q = (
        select(Insight.gaps)
        .where(Insight.created_at >= since)
        .where(Insight.skipped_reason.is_(None))
        .where(Insight.gaps.isnot(None))
    )
    gaps_r = await db.execute(gaps_q)
    gap_counts: dict[str, int] = {}
    for row in gaps_r.all():
        val = row[0] if len(row) else None
        for g in (val or []):
            if isinstance(g, str):
                gap_counts[g] = gap_counts.get(g, 0) + 1
    top_gaps = [{"gap": k, "count": v} for k, v in sorted(gap_counts.items(), key=lambda x: -x[1])[:20]]
    # Top topics
    topics_q = (
        select(Insight.topics)
        .where(Insight.created_at >= since)
        .where(Insight.skipped_reason.is_(None))
        .where(Insight.topics.isnot(None))
    )
    topics_r = await db.execute(topics_q)
    topic_counts: dict[str, int] = {}
    for row in topics_r.all():
        val = row[0] if len(row) else None
        for t in (val or []):
            if isinstance(t, str):
                topic_counts[t] = topic_counts.get(t, 0) + 1
    top_topics = [{"topic": k, "count": v} for k, v in sorted(topic_counts.items(), key=lambda x: -x[1])[:20]]
    return TrendsResponse(
        window=window,
        volume=volume,
        sentiment_drift=sentiment_drift,
        top_gaps=top_gaps,
        top_topics=top_topics,
    )
