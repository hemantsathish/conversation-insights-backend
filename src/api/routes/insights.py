"""
GET /api/v1/insights with filtering.

Query params: conversation_id, date_from, date_to, sentiment, topic, limit, offset.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import InsightOut, InsightsListResponse
from src.db.models import Insight
from src.db.session import get_db

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("", response_model=InsightsListResponse)
async def get_insights(
    db: AsyncSession = Depends(get_db),
    conversation_id: Optional[str] = Query(None, description="Filter by conversation id"),
    date_from: Optional[datetime] = Query(None, description="Insights created on or after"),
    date_to: Optional[datetime] = Query(None, description="Insights created before"),
    sentiment: Optional[str] = Query(None, description="Filter by sentiment (e.g. positive, negative)"),
    topic: Optional[str] = Query(None, description="Filter by topic (substring in topics list)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> InsightsListResponse:
    """
    List insights with optional filters. Paginated via limit/offset.
    """
    base = select(Insight).where(Insight.skipped_reason.is_(None))
    if conversation_id:
        base = base.where(Insight.conversation_id == conversation_id)
    if date_from:
        base = base.where(Insight.created_at >= date_from)
    if date_to:
        base = base.where(Insight.created_at < date_to)
    if sentiment:
        base = base.where(Insight.sentiment == sentiment)
    if topic:
        base = base.where(Insight.topics.contains([topic]))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    q = base.order_by(Insight.created_at.desc()).limit(limit).offset(offset)
    r = await db.execute(q)
    items = list(r.scalars().all())
    return InsightsListResponse(
        items=[InsightOut.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
