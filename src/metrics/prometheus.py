"""
Prometheus-style metrics: latency histograms, Grok success/error, token/cost, queue depth.

Exposed at GET /metrics. Instrument API latency, Grok calls, backpressure, queue depth.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# Request latency (API)
REQUEST_LATENCY = Histogram(
    "conversation_insights_request_duration_seconds",
    "Request latency in seconds",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# Grok calls
GROK_REQUESTS_TOTAL = Counter(
    "conversation_insights_grok_requests_total",
    "Total Grok API requests",
    ["status"],  # success, error
)
GROK_TOKENS_TOTAL = Counter(
    "conversation_insights_grok_tokens_total",
    "Total tokens (prompt + completion) sent to Grok",
)
GROK_COST_ESTIMATE_TOTAL = Counter(
    "conversation_insights_grok_cost_estimate_total",
    "Estimated cost (USD) from Grok usage",
)

# Queue and backpressure
QUEUE_DEPTH = Gauge(
    "conversation_insights_queue_depth",
    "Current number of conversation IDs in the analysis queue",
)
BACKPRESSURE_EVENTS_TOTAL = Counter(
    "conversation_insights_backpressure_events_total",
    "Times ingest was rejected due to queue full",
)


def get_metrics_bytes() -> bytes:
    """Return Prometheus text format (for GET /metrics)."""
    return generate_latest()


def update_queue_depth(depth: int) -> None:
    """Update queue depth gauge (call from middleware or periodic task)."""
    QUEUE_DEPTH.set(depth)


def record_backpressure() -> None:
    """Increment backpressure counter (when rejecting ingest)."""
    BACKPRESSURE_EVENTS_TOTAL.inc()


def record_grok_success(tokens: int = 0, cost: float | None = None) -> None:
    """Record successful Grok call."""
    GROK_REQUESTS_TOTAL.labels(status="success").inc()
    if tokens:
        GROK_TOKENS_TOTAL.inc(tokens)
    if cost is not None:
        GROK_COST_ESTIMATE_TOTAL.inc(cost)


def record_grok_error() -> None:
    """Record failed Grok call."""
    GROK_REQUESTS_TOTAL.labels(status="error").inc()
