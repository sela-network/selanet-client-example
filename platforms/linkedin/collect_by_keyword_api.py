"""
LinkedIn — Collect Posts by Keyword (REST API version, no SDK)

Same functionality as collect_by_keyword.py but uses the Selanet REST API
directly via httpx instead of the selanet_sdk.

API endpoint: POST https://api.selanet.ai/v1/browse
Auth: Bearer <SELA_API_KEY>

Flow:
  1. Search LinkedIn for a keyword via API
  2. Save results to JSONL

Note: LinkedIn search results do not include individual post URLs,
so detail + comment fetching is not available (unlike X or XHS).

Usage:
  uv run platforms/linkedin/collect_by_keyword_api.py --keyword "AI" --count 10
  uv run platforms/linkedin/collect_by_keyword_api.py --keyword "python" --count 50
  uv run platforms/linkedin/collect_by_keyword_api.py --keyword "machine learning" --count 20 --date-posted past-week
  uv run platforms/linkedin/collect_by_keyword_api.py --keyword "startup" --count 30 --sort date

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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from dotenv import load_dotenv

from shared import create_logger, timed, save_jsonl

load_dotenv()


# ── Config ──────────────────────────────────────────────────────────
API_BASE_URL = "https://api.selanet.ai/v1"
DEFAULT_TIMEOUT_MS = 240_000   # 4 min per request
HTTP_TIMEOUT_SEC = 600         # 10 min httpx timeout
RETRY_COUNT = 1

LINKEDIN_SEARCH_BASE = "https://www.linkedin.com/search/results/content/"

REQUEST_DELAY_SEC = 5

# sort → LinkedIn sortBy parameter mapping (relevance is default, omitted from URL)
SORT_MAP = {
    "date": "date_posted",
}


def build_search_url(keyword: str, date_posted: str | None, sort: str) -> str:
    params = {"keywords": keyword}
    if date_posted:
        params["datePosted"] = date_posted
    if sort and sort != "relevance" and sort in SORT_MAP:
        params["sortBy"] = SORT_MAP[sort]

    return f"{LINKEDIN_SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


# ── API helpers ─────────────────────────────────────────────────────
def _headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


async def api_browse(client: httpx.AsyncClient, api_key: str, url: str,
                     count: int | None = None,
                     timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """Call POST /v1/browse and return the JSON response."""
    body: dict = {"url": url, "timeout_ms": timeout_ms}
    if count is not None:
        body["count"] = count

    resp = await client.post(
        f"{API_BASE_URL}/browse",
        headers=_headers(api_key),
        json=body,
        timeout=HTTP_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


# ── Search ──────────────────────────────────────────────────────────
async def search_posts(client, api_key, logger, keyword, date_posted, sort, count):
    log = logger.log
    search_url = build_search_url(keyword, date_posted, sort)
    log(f"\n[Step 1] Search: {search_url} (count={count})")

    response = None
    for attempt in range(1, RETRY_COUNT + 2):
        response, err = await timed(
            logger,
            f"POST /v1/browse (search, count={count}, attempt {attempt}/{RETRY_COUNT + 1})",
            api_browse(client, api_key, search_url, count=count, timeout_ms=DEFAULT_TIMEOUT_MS),
            timeout=HTTP_TIMEOUT_SEC,
        )
        if response:
            break
        if attempt < RETRY_COUNT + 1:
            log(f"  Retrying search in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    posts = []
    if response:
        content = response.get("content") or response.get("page", {}).get("content", [])
        log(f"  total content items: {len(content)}")
        for item in content:
            fields = item.get("fields", {})
            if isinstance(fields, str):
                fields = json.loads(fields)
            posts.append({"content_type": item.get("content_type", ""), **fields})
            author = fields.get("author_name", "")
            text = fields.get("content", "")[:60]
            reactions = fields.get("reactions_count", "N/A")
            log(f"    {item.get('content_type')}: {author} — {text} | reactions={reactions}")

        stats = response.get("collection_stats")
        if stats:
            if isinstance(stats, str):
                stats = json.loads(stats)
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
    output_dir = Path(f"output/linkedin_keyword_api_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log

    t_start = time.time()

    async with httpx.AsyncClient() as client:
        try:
            log("=" * 60)
            log(f"LinkedIn Collect by Keyword (API) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            log(f"  keyword:     {keyword}")
            if date_posted:
                log(f"  date_posted: {date_posted}")
            log(f"  sort:        {sort}")
            log(f"  count:       {count} posts")
            log(f"  output:      {output_dir}")
            log("=" * 60)

            # ── Step 1: Search ────────────────────────────────────────
            posts = await search_posts(client, api_key, logger, keyword, date_posted, sort, count)
            if not posts:
                return

            save_jsonl(posts, output_dir / "search_results.jsonl")
            log(f"  Saved {len(posts)} posts → {output_dir / 'search_results.jsonl'}")

            # ── Summary ───────────────────────────────────────────────
            elapsed = time.time() - t_start

            log("\n" + "=" * 60)
            log("Summary:")
            log(f"  Search results: {len(posts)} posts")
            log(f"  Output dir: {output_dir}")
            log(f"  Elapsed: {elapsed:.1f}s")
            log("=" * 60)

            # ── Sample output ─────────────────────────────────────
            print("\n" + "=" * 60)
            print("Sample collected data")
            print("=" * 60)

            for i, post in enumerate(posts[:3]):
                author = post.get("author_name", "N/A")
                headline = post.get("author_headline", "")
                content = post.get("content", "N/A")
                print(f"\n[Post {i+1}] {author}")
                if headline:
                    print(f"  headline:  {headline[:100]}{'...' if len(headline) > 100 else ''}")
                print(f"  content:   {content[:120]}{'...' if len(content) > 120 else ''}")
                print(f"  reactions: {post.get('reactions_count', 'N/A')}")
                print(f"  reposts:   {post.get('reposts_count', 'N/A')}")
                print(f"  comments:  {post.get('comments_count', 'N/A')}")
                print(f"  posted:    {post.get('posted_time', 'N/A')}")

            print("=" * 60)

        finally:
            log(f"\nLog saved → {output_dir / 'collect.log'}")
            logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn — Collect Posts by Keyword (API)")
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
