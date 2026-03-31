"""
LinkedIn — Collect Posts by Keyword

Flow:
  1. Initialize client
  2. Search LinkedIn for a keyword via URL
  3. Save results to JSONL

Note: LinkedIn search results do not include individual post URLs,
so detail + comment fetching is not available (unlike X or XHS).

Usage:
  uv run platforms/linkedin/collect_by_keyword.py --keyword "AI" --count 10
  uv run platforms/linkedin/collect_by_keyword.py --keyword "python" --count 50
  uv run platforms/linkedin/collect_by_keyword.py --keyword "machine learning" --count 20 --date-posted past-week
  uv run platforms/linkedin/collect_by_keyword.py --keyword "startup" --count 30 --sort date

Date posted: past-24h, past-week, past-month
Sort:        relevance, date
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

# Ensure project root is importable when run from any directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from selanet_sdk import SelaClient, BrowseOptions

from shared import create_logger, timed, save_jsonl

load_dotenv()


# ── Config ──────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_MS = 240_000   # 4 min per request
ASYNC_TIMEOUT_SEC = 600        # 10 min async timeout for timed() calls
REQUEST_DELAY_SEC = 5          # delay between sequential requests (rate limiting)
RETRY_COUNT = 1                # number of retries on failure (1 = try once more)

LINKEDIN_SEARCH_BASE = "https://www.linkedin.com/search/results/content/"

# sort → LinkedIn sortBy parameter mapping (relevance is default, omitted from URL)
SORT_MAP = {
    "date": "date_posted",
}


def build_search_url(keyword: str, date_posted: str | None, sort: str) -> str:
    """Build LinkedIn search URL with filters encoded as query parameters."""
    params = {"keywords": keyword}
    if date_posted:
        params["datePosted"] = date_posted
    if sort and sort != "relevance" and sort in SORT_MAP:
        params["sortBy"] = SORT_MAP[sort]

    return f"{LINKEDIN_SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


# ── Search ──────────────────────────────────────────────────────────
async def search_posts(client, logger, keyword, date_posted, sort, count):
    """Search LinkedIn for posts matching a keyword. Returns list of post dicts."""
    log = logger.log
    search_url = build_search_url(keyword, date_posted, sort)
    log(f"\n[Step 2] Search: {search_url} (count={count})")

    search_response = None
    for attempt in range(1, RETRY_COUNT + 2):
        search_response, err = await timed(
            logger,
            f"client.browse (search, count={count}, attempt {attempt}/{RETRY_COUNT + 1})",
            client.browse(
                url=search_url,
                options=BrowseOptions(count=count, timeout_ms=DEFAULT_TIMEOUT_MS),
            ),
            timeout=ASYNC_TIMEOUT_SEC,
        )
        if search_response:
            break
        if attempt < RETRY_COUNT + 1:
            log(f"  Retrying search in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    posts = []
    if search_response and search_response.page:
        log(f"  total content items: {len(search_response.page.content)}")
        for item in search_response.page.content:
            fields = json.loads(item.fields_json)
            posts.append({"content_type": item.content_type, **fields})
            author = fields.get("author_name", "")
            content = fields.get("content", "")[:60]
            reactions = fields.get("reactions_count", "N/A")
            log(f"    {item.content_type}: {author} — {content} | reactions={reactions}")

        if search_response.collection_stats:
            stats = json.loads(search_response.collection_stats)
            log(f"  Collection stats: {stats}")
    else:
        log(f"  Search failed: {err}")

    return posts


# ── Main ────────────────────────────────────────────────────────────
async def collect_by_keyword(
    keyword: str,
    date_posted: str | None,
    sort: str,
    count: int,
):
    api_key = os.getenv("SELA_API_KEY")
    if not api_key:
        print("ERROR: SELA_API_KEY not set in .env")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"output/linkedin_keyword_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log
    client = None

    t_start = time.time()

    try:
        log("=" * 60)
        log(f"LinkedIn Collect by Keyword — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"  keyword:     {keyword}")
        if date_posted:
            log(f"  date_posted: {date_posted}")
        log(f"  sort:        {sort}")
        log(f"  count:       {count} posts")
        log(f"  output:      {output_dir}")
        log("=" * 60)

        # ── Step 1: Initialize client ─────────────────────────────
        log("\n[Step 1] Initialize client")
        client, err = await timed(logger, "SelaClient.with_api_key", SelaClient.with_api_key(api_key), timeout=30)
        if not client:
            log(f"  FATAL: Cannot create client: {err}")
            return

        # ── Step 2: Search ─────────────────────────────────────────
        posts = await search_posts(client, logger, keyword, date_posted, sort, count)
        if not posts:
            return

        save_jsonl(posts, output_dir / "search_results.jsonl")
        log(f"  Saved {len(posts)} posts → {output_dir / 'search_results.jsonl'}")

        # ── Summary ────────────────────────────────────────────────
        elapsed = time.time() - t_start

        log("\n" + "=" * 60)
        log("Summary:")
        log(f"  Search results: {len(posts)} posts")
        log(f"  Output dir: {output_dir}")
        log(f"  Elapsed: {elapsed:.1f}s")
        log("=" * 60)

    finally:
        if client:
            log("\n[Cleanup] Shutting down...")
            await timed(logger, "client.shutdown()", client.shutdown(timeout_ms=15_000), timeout=30)
        log(f"\nLog saved → {output_dir / 'collect.log'}")
        logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn — Collect Posts by Keyword")
    parser.add_argument("--keyword", default="AI", help="Search keyword")
    parser.add_argument("--date-posted", default=None,
                        choices=["past-24h", "past-week", "past-month"],
                        help="Date posted filter")
    parser.add_argument("--sort", default="relevance",
                        choices=["relevance", "date"],
                        help="Sort order (default: relevance)")
    parser.add_argument("--count", type=int, default=10, help="Number of posts to search")
    args = parser.parse_args()

    asyncio.run(collect_by_keyword(args.keyword, args.date_posted, args.sort, args.count))
