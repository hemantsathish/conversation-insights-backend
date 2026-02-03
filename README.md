# Conversation Insights Backend

REST API that ingests X/Twitter conversation data, stores threads, and generates insights via the Grok API. Async processing, backpressure, rate limiting, observability.

## Docs

- [docs/DEMO.md](docs/DEMO.md) - Demo walkthrough and UI guide
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Architecture and trade-offs
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) - Deployment (Dockerfile, docker-compose, .env)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Troubleshooting guide

## Quick start

1. Copy `.env.example` to `.env` and set `GROK_API_KEY`:
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and add your x.ai API key (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).
2. Run: `docker-compose up --build`
3. Open http://localhost:8000/app (dashboard). Use Health, Ingest, Insights, Trends from the UI.

For large CSV loads (e.g. Kaggle twcs.csv), run from a terminal:

```bash
python scripts/load_kaggle_subset.py --csv data/twcs.csv --limit 500
```

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/conversations | Single conversation |
| POST | /api/v1/conversations/bulk | Up to 500 (JSON array) |
| POST | /api/v1/conversations/bulk/stream | NDJSON body, streamed response |
| GET | /api/v1/insights | List insights (filters, pagination) |
| GET | /api/v1/trends | Time-windowed aggregates |
| GET | /metrics | Prometheus |
| GET | /health | Status and queue depth |
