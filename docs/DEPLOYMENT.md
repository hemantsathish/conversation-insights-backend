# Deployment

## Prerequisites

- Docker and Docker Compose (or Python 3.10+ for local run)
- PostgreSQL 16 (included in docker-compose)
- Grok API key from [console.x.ai](https://console.x.ai) (Manage, Billing, Redeem promo if needed)

## Environment variables

Copy `.env.example` to `.env` and set your Grok API key. Do not commit `.env` (it is in `.gitignore`).

```bash
cp .env.example .env
```

Edit `.env` and set `GROK_API_KEY` (required). All other variables in `.env.example` are documented there; uncomment and change as needed.

**Required:**

| Variable | Description |
|----------|-------------|
| DATABASE_URL | Async PostgreSQL URL, e.g. `postgresql+asyncpg://user:password@localhost:5432/conversation_insights` |
| GROK_API_KEY | x.ai API key from console.x.ai |

**Optional (Grok):** GROK_MODEL, GROK_RPM, GROK_TPM (see `.env.example`).

**Optional (other):** RATE_LIMIT_RPM, MAX_QUEUE_DEPTH, REDIS_URL (see [ARCHITECTURE](ARCHITECTURE.md) for Redis). If unset, in-memory queue is used.

## Dockerfile and docker-compose

The repo includes:

- **Dockerfile**: Python 3.12-slim image; installs app with `pip install -e .`; runs `uvicorn` with a single worker (in-memory queue is per-process).
- **docker-compose.yml**: Defines `api` (FastAPI app) and `db` (PostgreSQL 16). The `api` service uses `env_file: .env` and overrides `DATABASE_URL` to point at the `db` service. Volume `pgdata` persists database data.

## Build and run with Docker Compose

```bash
# From project root
cp .env.example .env
# Edit .env and set GROK_API_KEY
docker-compose up --build
```

- API: http://localhost:8000  
- Health: http://localhost:8000/health  
- Metrics: http://localhost:8000/metrics  
- OpenAPI: http://localhost:8000/docs  
- Web UI: http://localhost:8000/app  

Database data is persisted in volume `pgdata`.

Run a single API worker when using the in-memory queue; otherwise ingest and health can hit different processes and `queue_depth` will not reflect what was enqueued. For multiple workers, use a shared queue (Redis) and set `REDIS_URL` (see [ARCHITECTURE](ARCHITECTURE.md)).

## Local run (without Docker)

```bash
# PostgreSQL must be running; set DATABASE_URL and GROK_API_KEY in .env
pip install -e .
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Tables are created on startup (`init_db()`). For production, use Alembic migrations instead.

## Health and readiness

- **Health**: `GET /health` returns `{"status": "ok", "queue_depth": N, "process_id": ...}`. Use for load balancer health checks.
- **Readiness**: Same endpoint; if the app is up, it can accept traffic.

## Load data

After the API is running:

```bash
# Load from Kaggle twcs.csv (default path: data/twcs.csv)
python scripts/load_kaggle_subset.py --csv data/twcs.csv --limit 5000

# Simulate load (bulk POSTs, then /health and /metrics)
python scripts/simulate_load.py --bulks 10
```

Streaming ingest: POST NDJSON (one conversation per line) to `/api/v1/conversations/bulk/stream`. Response is NDJSON (one result per line, then `_summary` line).
