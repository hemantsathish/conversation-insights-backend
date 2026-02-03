"""
Cheap heuristic pre-filter: only "interesting" conversations go to Grok.

Reduces cost by skipping very short or trivial threads.
"""

from dataclasses import dataclass
from typing import List

from src.config import get_settings


@dataclass
class PreFilterResult:
    """Result of pre-filter check."""

    interesting: bool
    reason: str  # "ok" or skip reason


def pre_filter(
    message_count: int,
    total_chars: int,
    min_messages: int | None = None,
    min_chars: int | None = None,
) -> PreFilterResult:
    """
    Return whether the thread is interesting enough for Grok.

    Uses min message count and min total character count.
    """
    cfg = get_settings()
    min_messages = min_messages if min_messages is not None else cfg.PRE_FILTER_MIN_MESSAGES
    min_chars = min_chars if min_chars is not None else cfg.PRE_FILTER_MIN_TOTAL_CHARS
    if message_count < min_messages:
        return PreFilterResult(interesting=False, reason=f"message_count_{message_count}_lt_{min_messages}")
    if total_chars < min_chars:
        return PreFilterResult(interesting=False, reason=f"total_chars_{total_chars}_lt_{min_chars}")
    return PreFilterResult(interesting=True, reason="ok")


def pre_filter_thread(texts: List[str]) -> PreFilterResult:
    """Convenience: check list of message texts."""
    total_chars = sum(len(t or "") for t in texts)
    return pre_filter(message_count=len(texts), total_chars=total_chars)
