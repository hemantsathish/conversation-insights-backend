"""
FastAPI application entry point.

Mounts API routes (/api/v1/conversations, /api/v1/insights, /api/v1/trends),
/metrics, /health; web UI at /app; rate limit and latency middleware;
lifespan for DB init and background worker.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from src.api.middleware import RateLimitMiddleware, RequestLatencyMiddleware
from src.api.routes import conversations, insights, metrics, trends
from src.db.session import close_db, init_db
from src.processing.worker import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, start worker task. Shutdown: cancel worker, close DB."""
    await init_db()
    logger.info("Database initialized")
    task = asyncio.create_task(worker_loop())
    logger.info("Worker task started")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await close_db()
    logger.info("Database closed")


app = FastAPI(
    title="Conversation Insights API",
    description=(
        "REST API to ingest X/Twitter conversation data, store threads in PostgreSQL, "
        "and generate insights via the Grok API. Supports single and bulk ingest (array or NDJSON stream), "
        "filtered and paginated insights, time-windowed trends, Prometheus metrics, and a web UI at /app."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestLatencyMiddleware)
app.add_middleware(RateLimitMiddleware)

app.include_router(conversations.router, prefix="/api/v1")
app.include_router(insights.router, prefix="/api/v1")
app.include_router(trends.router, prefix="/api/v1")
app.include_router(metrics.router)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/", include_in_schema=False)
def app_root():
    """Redirect to web dashboard."""
    return RedirectResponse(url="/app", status_code=302)


@app.get("/app", include_in_schema=False)
def app_dashboard():
    """Serve dashboard (main web UI)."""
    return FileResponse(_STATIC_DIR / "dashboard.html", media_type="text/html")


@app.get("/app/insights", include_in_schema=False)
def app_insights():
    """Serve insights page."""
    return FileResponse(_STATIC_DIR / "insights.html", media_type="text/html")


@app.get("/app/trends", include_in_schema=False)
def app_trends():
    """Serve trends page."""
    return FileResponse(_STATIC_DIR / "trends.html", media_type="text/html")


@app.get("/app/ingest", include_in_schema=False)
def app_ingest():
    """Serve ingest page."""
    return FileResponse(_STATIC_DIR / "ingest.html", media_type="text/html")


@app.get("/app/health", include_in_schema=False)
def app_health_page():
    """Serve health page."""
    return FileResponse(_STATIC_DIR / "health.html", media_type="text/html")


@app.get("/health")
def health():
    """Health check for load balancers and docker healthcheck."""
    import os
    from src.ingestion.queue import get_queue
    q = get_queue()
    return {"status": "ok", "queue_depth": q.depth(), "process_id": os.getpid()}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return 500 with generic message; log traceback."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred."},
    )
