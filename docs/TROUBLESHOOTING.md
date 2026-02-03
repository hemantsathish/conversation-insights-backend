# Troubleshooting

## Database connection failures

- **Symptom**: Startup error or "connection refused" when calling the API.
- **Checks**:
  - PostgreSQL is running and reachable at the host/port in `DATABASE_URL`.
  - For Docker: use host `db` (service name) inside the api service; use `localhost` when running the API on the host.
  - URL must use the async driver: `postgresql+asyncpg://...`.
- **Fix**: Set `DATABASE_URL` correctly in `.env` and restart. With Docker Compose, the compose file overrides `DATABASE_URL` for the api service to point at the `db` service.

## Grok API errors and rate limits

- **Symptom**: Worker logs "Grok error" or "circuit_open"; insights not created.
- **Checks**:
  - `GROK_API_KEY` is set in `.env` and valid at [console.x.ai](https://console.x.ai).
  - 429 responses mean you exceeded RPM/TPM for your tier. Set `GROK_RPM` (and optionally `GROK_TPM`) in `.env` to stay under your limits (see Console, Models).
  - Circuit breaker opens after several failures; it retries after `GROK_CIRCUIT_BREAKER_COOLDOWN_SECONDS` (default 60).
- **Fix**: Reduce load, increase cooldown, or request higher limits from x.ai support.

## Queue backpressure and 503

- **Symptom**: Ingest returns 503 or `backpressure: true`; some items not enqueued.
- **Behaviour**: When queue depth >= `MAX_QUEUE_DEPTH`, single and bulk return 503 with Retry-After. Bulk/stream continues but sets `enqueued=false` for items that could not be enqueued and records backpressure.
- **Fix**: Increase `MAX_QUEUE_DEPTH` in `.env` or scale workers to drain the queue faster.

## Queue depth stays 0 after ingest (Accepted > 0)

- **Symptom**: Load script reports "Accepted=50" but `/health` shows `queue_depth: 0`.
- **Cause**: The queue is in-memory and per-process. If you run multiple API workers (e.g. `uvicorn ... --workers 2`), POST and GET can hit different processes: one enqueues, the other reports its own empty queue.
- **Fix**: Run a single API process (Docker Compose does this by default). For multiple workers, set `REDIS_URL` so all workers share one queue (see [ARCHITECTURE](ARCHITECTURE.md)).

## Empty thread for conversation_id

- **Symptom**: Worker logs "Empty thread for conversation_id=...".
- **Cause**: Conversation row exists but no Tweet rows found. This used to happen when the API enqueued before committing; the worker then read in a different transaction and saw no tweets.
- **Fix**: The code now commits before enqueueing (single and bulk). If you still see this, ensure you are on the latest code and that the DB is not being reset between ingest and worker run.

## Insights empty or only skipped_reason

- **Symptom**: GET /api/v1/insights returns rows but only with `skipped_reason` (e.g. "message_count_1_lt_2", "total_chars_...") and no sentiment/topics.
- **Cause**: Pre-filter skips very short threads (configurable `PRE_FILTER_MIN_MESSAGES`, `PRE_FILTER_MIN_TOTAL_CHARS`). Or Grok is not called (missing key, circuit open, or cache hit).
- **Fix**: Ingest conversations with at least 2 messages and enough text to pass the pre-filter; or lower the pre-filter thresholds in config. Ensure `GROK_API_KEY` is set and circuit breaker has not opened.

## Metrics interpretation

- **conversation_insights_request_duration_seconds**: Latency of API requests by method and path. Use for p50/p95/p99 (Prometheus histograms).
- **conversation_insights_grok_requests_total{status="success|error"}**: Grok call outcomes.
- **conversation_insights_queue_depth**: Current in-memory queue depth (gauge).
- **conversation_insights_backpressure_events_total**: Count of backpressure events (enqueue rejected or 503).

See `GET /metrics` for full output.
