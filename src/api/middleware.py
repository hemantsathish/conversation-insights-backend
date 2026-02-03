"""
Rate limiting, request logging, and error handling middleware.

- Per-IP rate limit (RPM); 429 with Retry-After when exceeded.
- Request latency recorded for Prometheus.
- Global exception handler for 500 and validation/400/422.
"""

import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import get_settings
from src.metrics.prometheus import REQUEST_LATENCY, record_backpressure

logger = logging.getLogger(__name__)

_settings = get_settings()

# Per-IP request counts (minute window)
_rate: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60.0  # seconds


def _clean_old_entries(ip: str) -> None:
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW
    _rate[ip] = [t for t in _rate[ip] if t > cutoff]


def _is_rate_limited(ip: str) -> bool:
    _clean_old_entries(ip)
    return len(_rate[ip]) >= _settings.RATE_LIMIT_RPM


def _record_request(ip: str) -> None:
    _rate[ip].append(time.monotonic())


def _retry_after_seconds() -> int:
    return int(_RATE_WINDOW)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Return 429 with Retry-After when per-IP RPM exceeded."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/metrics") or request.url.path.startswith("/health") or request.url.path.startswith("/app") or request.url.path == "/":
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        if _is_rate_limited(ip):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Retry after the indicated time."},
                headers={"Retry-After": str(_retry_after_seconds())},
            )
        _record_request(ip)
        return await call_next(request)


class RequestLatencyMiddleware(BaseHTTPMiddleware):
    """Record request latency for Prometheus."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start
        path = request.url.path or "/"
        method = request.method or "GET"
        REQUEST_LATENCY.labels(method=method, path=path).observe(duration)
        return response
