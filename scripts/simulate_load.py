#!/usr/bin/env python3
"""
Simulate load for video demo: bulk POSTs, concurrent requests, backpressure trigger.

Usage (API must be running):
  python scripts/simulate_load.py [--base-url http://localhost:8000] [--concurrent 5] [--bulks 10]

Sends multiple bulk POSTs concurrently to increase queue depth and optionally
trigger backpressure; then GET /metrics and /health to show queue depth and latency.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_BASE = "http://localhost:8000"


def sample_bulk_payload(n: int = 50) -> dict:
    """Minimal valid bulk payload with n fake conversations (2 messages each)."""
    convs = []
    for i in range(n):
        root_id = f"sim_root_{i}"
        reply_id = f"sim_reply_{i}"
        convs.append({
            "messages": [
                {"tweet_id": root_id, "author_id": "user1", "text": f"Sample message {i} from customer.", "inbound": True},
                {"tweet_id": reply_id, "author_id": "brand", "text": f"Sample reply {i} from support.", "in_reply_to_id": root_id, "inbound": False},
            ],
        })
    return {"conversations": convs}


async def run_bulk(client: httpx.AsyncClient, base: str, n: int) -> tuple[int, int]:
    """POST one bulk of n conversations; return (accepted, rejected)."""
    url = f"{base}/api/v1/conversations/bulk"
    payload = sample_bulk_payload(n)
    r = await client.post(url, json=payload, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    return data.get("accepted", 0), data.get("rejected", 0)


async def main_async(base: str, concurrent: int, bulks: int) -> None:
    logger.info("Starting simulate_load: base=%s concurrent=%s bulks=%s", base, concurrent, bulks)
    async with httpx.AsyncClient() as client:
        tasks = [run_bulk(client, base, 50) for _ in range(bulks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    total_ok = total_fail = 0
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("Bulk %s failed: %s", i + 1, r)
            total_fail += 1
        else:
            a, b = r
            total_ok += a
            total_fail += b
    logger.info("Bulk POSTs done. Total accepted=%s rejected=%s", total_ok, total_fail)
    async with httpx.AsyncClient() as client:
        health = await client.get(f"{base}/health", timeout=5.0)
        logger.info("Health: %s", health.json())
        metrics = await client.get(f"{base}/metrics", timeout=5.0)
        logger.info("Metrics (first 1500 chars): %s", (metrics.text or "")[:1500])


def main() -> None:
    p = argparse.ArgumentParser(description="Simulate load on Conversation Insights API")
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--concurrent", type=int, default=5, help="Concurrent bulk requests (not used yet; sequential for simplicity)")
    p.add_argument("--bulks", type=int, default=10, help="Number of bulk POSTs (50 convs each)")
    args = p.parse_args()
    asyncio.run(main_async(args.base_url, args.concurrent, args.bulks))


if __name__ == "__main__":
    main()
