"""
GET /metrics - Prometheus-style metrics (latency, errors, cost, queue depth).
"""

from fastapi import APIRouter, Response

from src.ingestion.queue import get_queue
from src.metrics.prometheus import get_metrics_bytes, update_queue_depth

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def get_metrics(response: Response) -> bytes:
    """Serve Prometheus text format. Update queue depth before export."""
    queue = get_queue()
    update_queue_depth(queue.depth())
    body = get_metrics_bytes()
    response.media_type = "text/plain; version=0.0.4; charset=utf-8"
    return body
