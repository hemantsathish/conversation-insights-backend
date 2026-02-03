"""
Grok API client: chat completions, thread-aware analysis, circuit breaker, retries.

Uses x.ai /v1/chat/completions (OpenAI-compatible). Builds a prompt from full
conversation thread and parses JSON insight (sentiment, topics, gaps).
"""

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Any, Optional

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# -----------------------------------------------------------------------------
# Circuit breaker
# -----------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "closed"  # normal
    OPEN = "open"     # failing, don't call
    HALF_OPEN = "half_open"  # one trial


class CircuitBreaker:
    """Stop calling Grok after N consecutive failures; retry after cooldown."""

    def __init__(
        self,
        failure_threshold: int,
        cooldown_seconds: float,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._last_open_at: Optional[float] = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def can_call(self) -> bool:
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if self._last_open_at and (time.monotonic() - self._last_open_at) >= self.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
                    return True
                return False
            return True  # half-open: allow one call

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failures = 0
            elif self._state == CircuitState.CLOSED:
                self._failures = 0

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_open_at = time.monotonic()
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._last_open_at = time.monotonic()


# -----------------------------------------------------------------------------
# Grok client
# -----------------------------------------------------------------------------

SYSTEM_PROMPT = """You analyze customer support conversation threads from Twitter/X.
Given a full thread (messages in order), output a JSON object with:
- "sentiment": one of "positive", "negative", "neutral", or "mixed"
- "topics": list of short topic strings (e.g. ["billing", "delay", "refund"])
- "gaps": list of service or communication gaps (e.g. "slow response", "no ETA")
- "summary": one short sentence summarizing the conversation

Output only valid JSON, no markdown or extra text."""


def _build_messages(thread_text: str) -> list[dict[str, str]]:
    """Build chat messages: system + user with full thread."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Conversation thread:\n\n{thread_text}"},
    ]


def _parse_insight_json(content: Optional[str]) -> dict[str, Any]:
    """Parse assistant content as JSON; return dict or empty dict on error."""
    if not content or not content.strip():
        return {}
    raw = content.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        start = 1 if lines[0].startswith("```json") else 0
        end = next((i for i, l in enumerate(lines) if i > 0 and l.strip() == "```"), len(lines))
        raw = "\n".join(lines[start:end])
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": content, "parse_error": True}


async def analyze_conversation(thread_text: str) -> dict[str, Any]:
    """
    Call Grok chat completions with full thread; return parsed insight and usage.

    Returns dict with keys: insight (dict), prompt_tokens, completion_tokens,
    total_tokens, cost_estimate (if in response), error (if failed).
    """
    cfg = get_settings()
    if not cfg.GROK_API_KEY:
        return {"error": "GROK_API_KEY not set", "insight": {}}

    circuit = get_circuit_breaker()
    if not await circuit.can_call():
        return {"error": "circuit_open", "insight": {}}

    url = f"{cfg.GROK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.GROK_API_KEY}",
    }
    payload = {
        "messages": _build_messages(thread_text),
        "model": cfg.GROK_MODEL,
        "stream": False,
        "temperature": 0,
    }

    last_error: Optional[str] = None
    for attempt in range(cfg.GROK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=cfg.GROK_TIMEOUT_SECONDS) as client:
                r = await client.post(url, headers=headers, json=payload)
            if r.status_code == 429:
                last_error = "rate_limit"
                await circuit.record_failure()
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code != 200:
                last_error = f"http_{r.status_code}"
                await circuit.record_failure()
                return {"error": last_error, "insight": {}, "status_code": r.status_code}
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                last_error = "no_choices"
                await circuit.record_failure()
                return {"error": last_error, "insight": {}}
            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""
            usage = data.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)
            cost_ticks = usage.get("cost_in_usd_ticks")  # optional
            cost_estimate = (cost_ticks / 1_000_000) if cost_ticks is not None else None
            insight = _parse_insight_json(content)
            await circuit.record_success()
            return {
                "insight": insight,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_estimate": cost_estimate,
            }
        except httpx.TimeoutException:
            last_error = "timeout"
            await circuit.record_failure()
            if attempt < cfg.GROK_MAX_RETRIES:
                await asyncio.sleep(1.0 * (attempt + 1))
        except Exception as e:
            last_error = str(e)
            await circuit.record_failure()
            logger.exception("Grok request failed")
            if attempt < cfg.GROK_MAX_RETRIES:
                await asyncio.sleep(1.0 * (attempt + 1))

    return {"error": last_error or "unknown", "insight": {}}


_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker(
            failure_threshold=get_settings().GROK_CIRCUIT_BREAKER_FAILURES,
            cooldown_seconds=get_settings().GROK_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )
    return _circuit_breaker
