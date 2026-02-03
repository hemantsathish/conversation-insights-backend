# Architecture and trade-offs

## High-level architecture

```
+-----------------------------------------------------------------+
|  CLIENT REQUESTS                                                 |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|  API LAYER (FastAPI)                                             |
|  POST /api/v1/conversations (single)                              |
|  POST /api/v1/conversations/bulk (up to 500, array)               |
|  POST /api/v1/conversations/bulk/stream (NDJSON, up to 500)       |
|  GET  /api/v1/insights (filtering)                                |
|  GET  /api/v1/trends (time-windowed aggregates)                   |
|  GET  /metrics (Prometheus)                                       |
|  GET  /health                                                    |
|  Rate limiting (429 + Retry-After), 400/422 error responses       |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|  INGESTION & QUEUE                                               |
|  Validate payloads, normalize to internal schema                |
|  Upsert conversations/tweets to DB                                |
|  Enqueue conversation_id (in-memory asyncio.Queue)                |
|  Backpressure: reject enqueue when depth >= MAX_QUEUE_DEPTH      |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|  PERSISTENT STORAGE (PostgreSQL)                                  |
|  conversations (id, root_tweet_id, created_at, updated_at)       |
|  tweets (id, conversation_id, author_id, text, in_reply_to_id,   |
|          inbound, created_at)                                     |
|  insights (conversation_id, grok_output JSONB, sentiment,        |
|            topics, gaps, cost_estimate, skipped_reason)           |
|  analysis_cache (thread_hash, conversation_id)                     |
|  Incremental updates: new tweets linked to existing conversation  |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|  ASYNC WORKER (background task)                                   |
|  Dequeue conversation_id -> load thread from DB                   |
|  Pre-filter (min messages, min chars) -> skip if not interesting  |
|  Cache lookup (thread_hash) -> reuse insight or skip              |
|  Grok API (chat completions) -> persist insight + cache           |
|  Circuit breaker, retries, RPM throttling                        |
+-----------------------------------------------------------------+
                                |
                                v
+-----------------------------------------------------------------+
|  GROK API (x.ai)                                                 |
|  POST /v1/chat/completions (model: grok-4-latest or grok-2-1212) |
|  Thread-aware prompt; flexible JSON output (sentiment, topics,   |
|  gaps, summary)                                                   |
+-----------------------------------------------------------------+
```

## Data flow

1. **Ingest**: Client POSTs single, bulk (array), or bulk/stream (NDJSON). API validates, normalizes, upserts to DB, commits, then enqueues. If queue full, we return 503 (single/bulk) or record backpressure and continue (stream); backpressure metric recorded.
2. **Worker**: Background loop dequeues conversation_id, loads full thread from DB, runs pre-filter (cheap heuristic). If not interesting, persists insight with skipped_reason and exits. Else checks analysis_cache by thread_hash; on hit reuses existing insight or copies. On miss, calls Grok chat completions with full thread text, parses JSON (sentiment, topics, gaps), persists insight and cache entry. Rate limiting via BatchController (RPM); circuit breaker stops calls after N failures and retries after cooldown.
3. **Read**: GET /insights (filter by conversation_id, date, sentiment, topic; paginated). GET /trends (time window: volume, sentiment drift, top gaps, top topics).

## Schema and thread reconstruction

- **Conversation**: One row per thread; `root_tweet_id` is the top-level tweet (unique). Thread is reconstructed by linking Tweet rows via `in_reply_to_id` (tweet -> reply -> reply).
- **Tweet**: `id` is the source tweet_id; `conversation_id` FK to conversations.id. Order for display: by `created_at` (or in_reply_to_id chain).
- **Insight**: One per conversation; Grok output (JSONB), extracted sentiment/topics/gaps, token/cost. `skipped_reason` set when pre-filter or cache skip.
- **AnalysisCache**: Keyed by hash of normalized thread text; avoids re-calling Grok for duplicate content.

## Trade-offs

- **In-memory queue**: Single process; no Redis required. Queue is per-process; run one API worker so ingest and health see the same queue. For multiple API workers (horizontal scale), set `REDIS_URL` in `.env` so all workers share one queue; otherwise each worker has its own in-memory queue and `queue_depth` / ingest behaviour will be inconsistent across requests.
- **Commit before enqueue**: Bulk/single handlers commit DB transaction before enqueueing so the worker always sees persisted conversations and tweets.
- **Pre-filter**: Skips very short threads (configurable min messages, min chars) to reduce Grok cost.
- **Circuit breaker**: Stops Grok calls after repeated failures; half-open retry after cooldown.
