"""
Application configuration from environment variables.

Uses pydantic-settings for validation and defaults. All secrets and
tunables (DB, Redis, Grok API, rate limits, queue depth) are read here.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://user:password@localhost:5432/conversation_insights",
        description="Async PostgreSQL connection URL (asyncpg driver).",
    )

    # -------------------------------------------------------------------------
    # Queue (optional Redis; falls back to in-memory)
    # -------------------------------------------------------------------------
    REDIS_URL: Optional[str] = Field(
        default=None,
        description="Redis URL for queue. If unset, in-memory asyncio queue is used.",
    )

    # -------------------------------------------------------------------------
    # Grok API (x.ai)
    # -------------------------------------------------------------------------
    GROK_API_KEY: str = Field(default="", description="x.ai API key from console.x.ai.")
    GROK_BASE_URL: str = Field(
        default="https://api.x.ai/v1",
        description="Grok API base URL.",
    )
    GROK_MODEL: str = Field(
        default="grok-4-latest",
        description="Model name for chat completions (e.g. grok-4-latest, grok-2-1212).",
    )
    GROK_RPM: int = Field(
        default=60,
        ge=1,
        le=10000,
        description="Max requests per minute to Grok (throttle to stay under your tier).",
    )
    GROK_TPM: Optional[int] = Field(
        default=None,
        ge=1,
        description="Max tokens per minute (optional; if set, we throttle by token estimate).",
    )
    GROK_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0, description="Request timeout for Grok API.")
    GROK_MAX_RETRIES: int = Field(default=3, ge=0, description="Retries with backoff on transient errors.")
    GROK_CIRCUIT_BREAKER_FAILURES: int = Field(
        default=5,
        ge=1,
        description="Failures before opening circuit (stop calling Grok).",
    )
    GROK_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = Field(
        default=60.0,
        gt=0,
        description="Seconds before half-open retry.",
    )

    # -------------------------------------------------------------------------
    # API rate limiting (per client)
    # -------------------------------------------------------------------------
    RATE_LIMIT_RPM: int = Field(
        default=60,
        ge=1,
        description="Max requests per minute per IP (or per key) for REST API.",
    )
    MAX_QUEUE_DEPTH: int = Field(
        default=10_000,
        ge=1,
        description="Max queue depth; beyond this we return 503 (backpressure).",
    )

    # -------------------------------------------------------------------------
    # Processing (pre-filter, cache, batching)
    # -------------------------------------------------------------------------
    PRE_FILTER_MIN_MESSAGES: int = Field(
        default=2,
        ge=1,
        description="Min messages in thread to be considered for Grok (cheap filter).",
    )
    PRE_FILTER_MIN_TOTAL_CHARS: int = Field(
        default=50,
        ge=0,
        description="Min total character count in thread for Grok.",
    )
    CACHE_TTL_SECONDS: Optional[int] = Field(
        default=None,
        description="TTL for analysis cache in seconds (None = no expiry).",
    )
    BATCH_MIN_SIZE: int = Field(default=1, ge=1, description="Min batch size for Grok.")
    BATCH_MAX_SIZE: int = Field(default=10, ge=1, description="Max batch size (adaptive batching cap).")
    WORKER_POLL_INTERVAL_SECONDS: float = Field(default=1.0, gt=0, description="Queue poll interval when empty.")

    # -------------------------------------------------------------------------
    # Bulk ingest
    # -------------------------------------------------------------------------
    BULK_MAX_CONVERSATIONS: int = Field(default=500, ge=1, le=500, description="Max conversations per bulk POST.")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance (loads env once)."""
    return Settings()
