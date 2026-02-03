"""
Adaptive batch size: grow when healthy, shrink on errors/latency spikes.

Used by worker to throttle concurrent Grok calls and respect RPM/TPM.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from src.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


class BatchController:
    """
    Tracks success/failure and latency to adjust effective concurrency.
    Controls concurrent single Grok calls and spacing (RPM); one conversation per API call.
    """

    def __init__(self) -> None:
        self.min_size = _settings.BATCH_MIN_SIZE
        self.max_size = _settings.BATCH_MAX_SIZE
        self.current = min(self.max_size, max(self.min_size, 2))
        self._successes = 0
        self._failures = 0
        self._last_latencies: list[float] = []
        self._max_latency_samples = 20
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()
        self._rpm_limit = _settings.GROK_RPM
        self._tpm_limit = _settings.GROK_TPM
        self._min_interval = 60.0 / self._rpm_limit if self._rpm_limit else 0
        self._last_call_at: float = 0
        self._tokens_this_minute: int = 0
        self._minute_start: float = time.monotonic()

    async def acquire(self) -> bool:
        """Wait until we can make one Grok call (rate limit). Returns True when allowed."""
        async with self._async_lock:
            now = time.monotonic()
            if self._min_interval > 0:
                elapsed = now - self._last_call_at
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
                self._last_call_at = time.monotonic()
            return True

    def record_success(self, latency_seconds: float, tokens: int = 0) -> None:
        """Record successful call; may increase concurrency."""
        with self._sync_lock:
            self._successes += 1
            self._last_latencies.append(latency_seconds)
            if len(self._last_latencies) > self._max_latency_samples:
                self._last_latencies.pop(0)
            self._tokens_this_minute += tokens
            p95 = self._p95_latency()
            if p95 is not None and p95 < 5.0 and self.current < self.max_size:
                self.current = min(self.max_size, self.current + 1)

    def record_failure(self) -> None:
        """Record failure; shrink concurrency."""
        with self._sync_lock:
            self._failures += 1
            self.current = max(self.min_size, self.current - 1)

    def _p95_latency(self) -> Optional[float]:
        if not self._last_latencies:
            return None
        s = sorted(self._last_latencies)
        idx = int(len(s) * 0.95) or 0
        return s[min(idx, len(s) - 1)]

    @property
    def current_batch_size(self) -> int:
        """Current allowed concurrent batch size (for logging)."""
        return self.current
