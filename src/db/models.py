"""
SQLAlchemy models for conversation threads and insights.

Schema supports:
- Full thread reconstruction via in_reply_to_id (tweet -> reply -> reply).
- Incremental updates: new tweets can be added to an existing conversation_id.
- Insights from Grok (JSON output, sentiment, topics, cost) plus cache key for dedup.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base for all ORM models."""

    pass


class Conversation(Base):
    """
    One conversation thread (root tweet + all replies).

    Thread is reconstructed by linking Tweet rows via in_reply_to_id.
    root_tweet_id identifies the top-level tweet; all replies share this conversation_id.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    root_tweet_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    tweets: Mapped[list["Tweet"]] = relationship(
        "Tweet",
        back_populates="conversation",
        order_by="Tweet.created_at",
    )
    insight: Mapped[Optional["Insight"]] = relationship(
        "Insight",
        back_populates="conversation",
        uselist=False,
    )


class Tweet(Base):
    """
    Single tweet (or reply) in a conversation.

    in_reply_to_id links to another Tweet in the same conversation (or null for root).
    Thread order: root has in_reply_to_id=None; replies point to parent tweet_id.
    """

    __tablename__ = "tweets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # tweet_id from source
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    in_reply_to_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    quoted_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # quoted tweet (tweet/reply/quote)
    inbound: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # customer vs brand
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at_raw: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # original string if needed

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="tweets")


class Insight(Base):
    """
    Grok analysis result for one conversation.

    grok_output is the raw JSON from Grok (flexible schema). We also store
    extracted fields (sentiment, topics) for filtering and trends.
    """

    __tablename__ = "insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    grok_output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sentiment: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)  # e.g. positive/negative/neutral
    topics: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)  # list of topic strings
    gaps: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)  # identified gaps
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # approximate cost (USD)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    skipped_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)  # if pre-filter skipped

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="insight")


class AnalysisCache(Base):
    """
    Cache keyed by hash of normalized thread text to avoid re-calling Grok.

    thread_hash = hash(concatenated normalized messages). On hit we reuse
    the referenced insight or skip analysis.
    """

    __tablename__ = "analysis_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    thread_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
