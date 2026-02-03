# Demo and UI guide

One doc: how to run, what happens when you hit what, and how to use the app entirely from the UI.

---

## 1. Run the app

1. Copy `.env.example` to `.env` and set `GROK_API_KEY`:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your x.ai API key (see [DEPLOYMENT](DEPLOYMENT.md)). For Docker, `DATABASE_URL` is overridden by compose.
2. From the project root: `docker-compose up --build`.
3. Wait until you see "Worker task started" and "Uvicorn running on http://0.0.0.0:8000".
4. Open http://localhost:8000 in the browser.

Root (`/`) redirects to `/app` (the dashboard).

---

## 2. What happens when you hit what

### Dashboard – http://localhost:8000/app

- **What you see:** Title "Conversation Insights", short description, nav links (Insights, Trends, Ingest, Health, API), quick-links card, link to OpenAPI and Prometheus.
- **What happens:** Static page. No API calls. Use this as home; all other UI pages link back here.

### Health – http://localhost:8000/app/health

- **What you see:** JSON: `status`, `queue_depth`, `process_id`.
- **What happens:** Page calls `GET /health`. Backend returns in-memory queue depth and process PID. Use this to confirm the app is up and to see how many conversations are waiting for analysis (queue goes up after ingest, down as the worker runs).

### Ingest – http://localhost:8000/app/ingest

- **Single conversation**
  - You paste JSON with a `messages` array (each message: `tweet_id`, `author_id`, `text`; optional `in_reply_to_id`, `inbound`).
  - You click "Submit single".
  - **What happens:** Page calls `POST /api/v1/conversations` with that JSON. Backend validates, normalizes, upserts conversation and tweets to the DB, commits, then enqueues the conversation for the worker. Response shows `conversation_id` and `enqueued: true` (or 503 if queue is full).
- **Bulk (up to 500)**
  - You paste JSON with a `conversations` array; each item has a `messages` array (same shape as above).
  - You click "Submit bulk".
  - **What happens:** Page calls `POST /api/v1/conversations/bulk`. Backend upserts all conversations and tweets in one transaction, commits, then enqueues each conversation. Response shows `accepted`, `rejected`, and `backpressure` if the queue was full for some.

After ingest, the background worker picks conversations from the queue, runs a pre-filter (skips very short threads), checks cache, calls Grok for the rest, and writes insights to the DB. This takes time (Grok rate limits apply).

### Insights – http://localhost:8000/app/insights

- **What you see:** Filters (Sentiment, Topic, Limit), Load button, then a table: Conversation (id prefix), Sentiment, Topics, Gaps, Tokens/Cost, Created. Pagination (Previous / Next).
- **What happens:** On Load (and on first load), page calls `GET /api/v1/insights?limit=...&offset=...` (and optional `sentiment=`, `topic=`). Backend returns analyzed conversations from the DB. Rows show sentiment/topics/gaps from Grok, or a skipped reason (e.g. "message_count_1_lt_2") if the pre-filter skipped. Empty at first; fills after you ingest and the worker runs.

### Trends – http://localhost:8000/app/trends

- **What you see:** Dropdown (Last 1 day / 7 days / 30 days), Load button, then sections: volume, sentiment drift, top gaps, top topics.
- **What happens:** Page calls `GET /api/v1/trends?window=1d|7d|30d`. Backend aggregates insights in that time window and returns counts and lists. Use this to see how volume and sentiment change over time and what topics/gaps show up most.

### API (Swagger) – http://localhost:8000/docs

- **What you see:** OpenAPI UI for all endpoints.
- **What happens:** Static docs + "Try it out" against the same backend. Same behavior as the UI; useful for debugging or scripting.

### Prometheus metrics – http://localhost:8000/metrics

- **What you see:** Plain-text metrics (request duration, Grok calls, queue depth, etc.).
- **What happens:** Backend returns current metric values. No UI; for monitoring tools.

---

## 3. Using the app entirely from the UI

You can do everything from the browser without scripts or curl.

1. **Start the app** (Docker as above), then open http://localhost:8000/app.
2. **Check health** – Click "Health". You should see `"status": "ok"` and `queue_depth: 0`.
3. **Ingest one conversation** – Click "Ingest". Under "Single conversation", paste for example:
   ```json
   {"messages":[{"tweet_id":"1","author_id":"u1","text":"My order never arrived.","inbound":true},{"tweet_id":"2","author_id":"brand","text":"We'll look into it.","in_reply_to_id":"1","inbound":false}]}
   ```
   Click "Submit single". You should see "Created: conversation_id=..., enqueued=true".
4. **Ingest more (bulk)** – Under "Bulk (up to 500)", paste JSON with a `conversations` array (each element like `{"messages":[...]}`). Click "Submit bulk". You should see "Accepted: N, Rejected: 0" (or similar).
5. **Watch the queue** – Click "Health" again. `queue_depth` should be greater than 0 right after ingest, then decrease as the worker runs.
6. **View insights** – Click "Insights", then "Load". After the worker has run, you’ll see rows (sentiment, topics, gaps or skipped reason). Use Sentiment / Topic filters and Load to narrow. Use Previous/Next to paginate.
7. **View trends** – Click "Trends", pick a window (e.g. Last 7 days), click "Load". You’ll see volume, sentiment drift, top gaps, and top topics for that window.

For large CSV loads (e.g. Kaggle twcs.csv), use the script from a terminal; the UI bulk box is for pasting smaller JSON payloads (up to 500 conversations). For API exploration or automation, use the "API (Swagger)" link from the dashboard.

---

## 4. Quick reference

| Where | What it does |
|-------|----------------|
| /app | Dashboard; links to all UI pages |
| /app/health | GET /health; status and queue depth |
| /app/ingest | POST single or bulk conversations |
| /app/insights | GET /api/v1/insights; list analyzed conversations |
| /app/trends | GET /api/v1/trends; time-window aggregates |
| /docs | OpenAPI UI |
| /metrics | Prometheus metrics |

If the queue never goes up after ingest, you’re likely running more than one API process (queue is per-process). Run a single worker (Docker Compose does by default). If insights stay empty, wait for the worker to run (and check Health for queue_depth); ensure `GROK_API_KEY` is set so Grok calls can succeed.
