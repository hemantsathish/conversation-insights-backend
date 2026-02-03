"""
Pydantic request/response schemas for conversations, insights, and trends.

Used for validation, OpenAPI docs, and serialization.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Ingest: single message (tweet/reply)
# -----------------------------------------------------------------------------


class MessageIn(BaseModel):
    """Single message in a conversation (tweet, reply, or quote)."""

    tweet_id: str = Field(..., min_length=1, max_length=64, description="Unique tweet id.")
    author_id: str = Field(..., min_length=1, max_length=64, description="Author user id.")
    text: str = Field(..., min_length=1, description="Message text.")
    in_reply_to_id: Optional[str] = Field(None, max_length=64, description="Parent tweet id if reply.")
    quoted_id: Optional[str] = Field(None, max_length=64, description="Quoted tweet id if quote.")
    inbound: bool = Field(True, description="True if customer, False if brand/support.")
    created_at: Optional[datetime] = Field(None, description="Timestamp (optional).")
    created_at_raw: Optional[str] = Field(None, max_length=64, description="Original timestamp string if needed.")


# -----------------------------------------------------------------------------
# Ingest: single conversation
# -----------------------------------------------------------------------------


class ConversationIn(BaseModel):
    """Single conversation: list of messages (root + replies)."""

    messages: list[MessageIn] = Field(..., min_length=1, max_length=500, description="Ordered messages in thread.")


# -----------------------------------------------------------------------------
# Ingest: bulk (array of conversations, max 500)
# -----------------------------------------------------------------------------


class BulkConversationsIn(BaseModel):
    """Bulk ingest: up to 500 conversations."""

    conversations: list[ConversationIn] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Up to 500 conversations.",
    )


# -----------------------------------------------------------------------------
# Ingest response
# -----------------------------------------------------------------------------


class IngestResultItem(BaseModel):
    """Result for one conversation in bulk ingest."""

    conversation_id: str
    root_tweet_id: str
    message_count: int
    enqueued: bool = True


class IngestResponse(BaseModel):
    """Response for single POST /conversations."""

    conversation_id: str
    root_tweet_id: str
    message_count: int
    enqueued: bool = True


class BulkIngestResponse(BaseModel):
    """Response for POST /conversations/bulk."""

    accepted: int
    rejected: int
    results: list[IngestResultItem] = Field(default_factory=list)
    backpressure: bool = Field(False, description="True if queue was full and some items were not enqueued.")


# -----------------------------------------------------------------------------
# Insights: query params and response
# -----------------------------------------------------------------------------


class InsightOut(BaseModel):
    """One insight (Grok analysis result)."""

    id: str
    conversation_id: str
    sentiment: Optional[str] = None
    topics: Optional[list[str]] = None
    gaps: Optional[list[str]] = None
    grok_output: dict[str, Any] = Field(default_factory=dict)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_estimate: Optional[float] = None
    created_at: datetime
    skipped_reason: Optional[str] = None

    model_config = {"from_attributes": True}


class InsightsListResponse(BaseModel):
    """Paginated list of insights."""

    items: list[InsightOut]
    total: int
    limit: int
    offset: int


# -----------------------------------------------------------------------------
# Trends: query params and response
# -----------------------------------------------------------------------------


class TrendVolumePoint(BaseModel):
    """Volume at a time bucket."""

    bucket: str  # e.g. "2024-01-15T00:00:00Z"
    count: int


class TrendSentimentPoint(BaseModel):
    """Sentiment distribution in a bucket."""

    bucket: str
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    other: int = 0


class TrendsResponse(BaseModel):
    """Time-windowed aggregates for GET /trends."""

    window: str = Field(..., description="e.g. 1d, 7d")
    volume: list[TrendVolumePoint] = Field(default_factory=list)
    sentiment_drift: list[TrendSentimentPoint] = Field(default_factory=list)
    top_gaps: list[dict[str, Any]] = Field(default_factory=list, description="Top gap keywords/categories.")
    top_topics: list[dict[str, Any]] = Field(default_factory=list, description="Top topics by count.")
