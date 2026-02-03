#!/usr/bin/env python3
"""
Load 5k–10k conversations from Kaggle Customer Support on Twitter (twcs.csv) into the API.

Usage (API must be running):
  python scripts/load_kaggle_subset.py --csv data/twcs.csv [--limit 5000] [--base-url http://localhost:8000]

Reconstructs conversation threads from tweet_id and in_response_to_tweet_id,
normalizes to internal schema, and POSTs to /api/v1/conversations/bulk in chunks of 500.
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.normalizer import get_root_tweet_id, twcs_row_to_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BULK_CHUNK = 500
DEFAULT_BASE = "http://localhost:8000"


def _find_root(tid: str, by_id: dict[str, dict]) -> str:
    """Follow in_response_to_tweet_id to root; return root tweet_id."""
    visited: set[str] = set()
    current = tid
    while current and current not in visited:
        visited.add(current)
        r = by_id.get(current)
        if not r:
            return current
        parent = (r.get("in_response_to_tweet_id") or "").strip()
        if not parent or parent not in by_id:
            return current
        current = parent
    return current or tid


def build_conversations_from_csv(csv_path: str, limit: int) -> list[list[dict]]:
    """
    Read twcs.csv and group rows into conversations by reply chain.
    Returns list of conversations; each conversation is list of message dicts (tweet_id, author_id, text, in_reply_to_id, inbound, created_at).
    """
    by_id: dict[str, dict] = {}
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = (row.get("tweet_id") or "").strip()
            if not tid:
                continue
            by_id[tid] = row
    # Group tweet_ids by root
    by_root: dict[str, list[str]] = {}
    for tid in by_id:
        root = _find_root(tid, by_id)
        by_root.setdefault(root, []).append(tid)
    # Build one conversation per root: order tweets by created_at if available, else by id
    conversations: list[list[dict]] = []
    for root, tids in by_root.items():
        if root not in by_id:
            continue
        rows = [by_id[t] for t in tids]
        try:
            rows.sort(key=lambda r: (r.get("created_at") or "", r.get("tweet_id") or ""))
        except Exception:
            pass
        msgs = [twcs_row_to_message(r) for r in rows]
        if not msgs:
            continue
        conversations.append(msgs)
        if len(conversations) >= limit:
            break
    return conversations


def messages_to_payload(conv: list[dict]) -> dict:
    """Convert internal message dicts to API BulkConversationsIn item."""
    return {
        "messages": [
            {
                "tweet_id": m["tweet_id"],
                "author_id": m["author_id"],
                "text": m["text"],
                "in_reply_to_id": m.get("in_reply_to_id"),
                "quoted_id": m.get("quoted_id"),
                "inbound": m.get("inbound", True),
                "created_at_raw": m.get("created_at_raw"),
            }
            for m in conv
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Load Kaggle twcs.csv into Conversation Insights API")
    p.add_argument("--csv", default="data/twcs.csv", help="Path to twcs.csv")
    p.add_argument("--limit", type=int, default=5000, help="Max conversations to load (default 5000)")
    p.add_argument("--base-url", default=DEFAULT_BASE, help="API base URL")
    p.add_argument("--dry-run", action="store_true", help="Only build conversations from CSV, do not POST")
    args = p.parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        sys.exit(1)
    logger.info("Building conversations from %s (limit=%s)", csv_path, args.limit)
    conversations = build_conversations_from_csv(str(csv_path), args.limit)
    logger.info("Built %s conversations", len(conversations))
    if len(conversations) == 0:
        logger.error(
            "No conversations built. CSV must have columns: tweet_id, author_id, inbound, created_at, text, in_response_to_tweet_id"
        )
        sys.exit(1)
    if args.dry_run:
        logger.info("Dry run: not sending to API")
        return
    base = args.base_url.rstrip("/")
    try:
        health = httpx.get(f"{base}/health", timeout=10.0)
        health.raise_for_status()
        data0 = health.json()
        q0 = data0.get("queue_depth", "?")
        pid0 = data0.get("process_id", "?")
        logger.info("API reachable. Queue depth before load: %s (process_id=%s)", q0, pid0)
    except Exception as e:
        logger.error("API not reachable at %s: %s. Is the server running?", base, e)
        sys.exit(1)
    url = f"{base}/api/v1/conversations/bulk"
    total_accepted = 0
    total_rejected = 0
    for i in range(0, len(conversations), BULK_CHUNK):
        chunk = conversations[i : i + BULK_CHUNK]
        payload = {"conversations": [messages_to_payload(c) for c in chunk]}
        try:
            r = httpx.post(url, json=payload, timeout=60.0)
            r.raise_for_status()
            data = r.json()
            a, b = data.get("accepted", 0), data.get("rejected", 0)
            total_accepted += a
            total_rejected += b
            if a == 0 and b > 0:
                logger.warning("Chunk %s: all %s rejected. First result: %s", i // BULK_CHUNK + 1, b, data.get("results", [None])[0])
            if data.get("backpressure"):
                logger.warning("Backpressure on chunk %s", i // BULK_CHUNK + 1)
        except httpx.HTTPStatusError as e:
            logger.exception("Chunk %s HTTP %s: %s", i // BULK_CHUNK + 1, e.response.status_code, e.response.text[:500])
            sys.exit(1)
        except Exception as e:
            logger.exception("Chunk %s failed: %s", i // BULK_CHUNK + 1, e)
            sys.exit(1)
    try:
        health2 = httpx.get(f"{base}/health", timeout=10.0)
        health2.raise_for_status()
        data1 = health2.json()
        q1 = data1.get("queue_depth", "?")
        pid1 = data1.get("process_id", "?")
        logger.info("Done. Accepted=%s Rejected=%s. Queue depth after load: %s (process_id=%s)", total_accepted, total_rejected, q1, pid1)
        if total_accepted > 0 and q1 == 0 and pid0 != pid1:
            logger.warning("Health before and after hit different processes (pid %s vs %s). Queue is per-process; use a single API worker.", pid0, pid1)
    except Exception:
        logger.info("Done. Accepted=%s Rejected=%s", total_accepted, total_rejected)


if __name__ == "__main__":
    main()
