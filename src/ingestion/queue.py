"""
Queue abstraction for conversation IDs to be analyzed.

Supports in-memory (asyncio.Queue) or Redis. Backpressure: when depth >= max_depth,
enqueue can reject (return False) or caller returns 503.
"""

import asyncio
import logging
from typing import Optional

from src.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


class ConversationQueue:
    """
    In-memory queue of conversation_id strings with backpressure.

    Thread-safe via asyncio.Queue. When depth >= max_depth, put_nowait would raise;
    we use put with timeout or check depth before enqueue and return False.
    """

    def __init__(
        self,
        max_depth: Optional[int] = None,
    ) -> None:
        self._max_depth = max_depth or _settings.MAX_QUEUE_DEPTH
        self._q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_depth)

    def depth(self) -> int:
        """Current number of items in queue."""
        return self._q.qsize()

    @property
    def max_depth(self) -> int:
        return self._max_depth

    def can_accept(self) -> bool:
        """True if enqueue would not exceed max_depth."""
        return self.depth() < self._max_depth

    def enqueue(self, conversation_id: str) -> bool:
        """
        Add conversation_id to queue (non-blocking). Returns False if queue full (backpressure).
        """
        if self.depth() >= self._max_depth:
            return False
        try:
            self._q.put_nowait(conversation_id)
            return True
        except asyncio.QueueFull:
            return False

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[str]:
        """Remove and return one conversation_id, or None if empty after timeout."""
        try:
            return await asyncio.wait_for(self._q.get(), timeout=timeout or _settings.WORKER_POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            return None

    def enqueue_many(self, conversation_ids: list[str]) -> tuple[int, int]:
        """
        Enqueue as many as possible. Returns (accepted, rejected).
        """
        accepted = 0
        for cid in conversation_ids:
            if self.enqueue(cid):
                accepted += 1
            else:
                break
        return accepted, len(conversation_ids) - accepted


_queue: Optional[ConversationQueue] = None


def get_queue() -> ConversationQueue:
    """Get or create the global conversation queue."""
    global _queue
    if _queue is None:
        _queue = ConversationQueue()
    return _queue
