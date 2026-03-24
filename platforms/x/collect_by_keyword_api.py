"""
X (Twitter) — Collect Tweets by Keyword (REST API version, no SDK)

Same functionality as collect_by_keyword.py but uses the Selanet REST API
directly via httpx instead of the selanet_sdk.

API endpoint: POST https://api.selanet.ai/v1/browse
Auth: Bearer <SELA_API_KEY>

Flow:
  1. Search X for a keyword via API
  2. For each tweet, fetch tweet detail + replies
  3. Save results to JSONL

Options:
  --parallel    Use asyncio.gather for concurrent requests. Default is sequential.
  --comments N  Number of replies per tweet. 0 = skip replies.

Usage:
  uv run platforms/x/collect_by_keyword_api.py --keyword "ai" --count 10 --comments 10 --parallel

Search tabs: Top, Latest, People, Media
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
BATCH_SIZE = 3
REQUEST_DELAY_SEC = 5
RETRY_COUNT = 1

X_SEARCH_BASE = "https://x.com/search"

SEARCH_TAB_MAP = {
    "Top": "top",
    "Latest": "live",
    "People": "user",
    "Media": "media",
}


def build_search_url(keyword: str, search_tab: str, lang: str | None,
                     since: str | None, until: str | None,
                     min_likes: int | None, min_retweets: int | None) -> str:
    query_parts = [keyword]
    if lang:
        query_parts.append(f"lang:{lang}")
    if since:
        query_parts.append(f"since:{since}")
    if until:
        query_parts.append(f"until:{until}")
    if min_likes:
        query_parts.append(f"min_faves:{min_likes}")
    if min_retweets:
        query_parts.append(f"min_retweets:{min_retweets}")

    params = {
        "q": " ".join(query_parts),
        "src": "typed_query",
        "f": SEARCH_TAB_MAP.get(search_tab, "top"),
    }
    return f"{X_SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


# ── API helpers ─────────────────────────────────────────────────────
def _headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


async def api_browse(client: httpx.AsyncClient, api_key: str, url: str,
                     count: int | None = None, parse_only: bool = False,
                     timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    """Call POST /v1/browse and return the JSON response."""
    body: dict = {"url": url, "timeout_ms": timeout_ms}
    if count is not None:
        body["count"] = count
    if parse_only:
        body["parse_only"] = parse_only

    resp = await client.post(
        f"{API_BASE_URL}/browse",
        headers=_headers(api_key),
        json=body,
        timeout=HTTP_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


def parse_response_content(response: dict):
    """Parse an API browse response into a dict with tweet detail and replies."""
    data = {"search_tweet": None, "detail": {}, "comments": []}
    content = response.get("content") or response.get("page", {}).get("content", [])
    for item in content:
        fields = item.get("fields", {})
        if isinstance(fields, str):
            fields = json.loads(fields)
        content_type = item.get("content_type", "")
        if content_type in ("tweet_detail", "tweet"):
            data["detail"] = fields
        elif content_type in ("comment", "reply"):
            data["comments"].append(fields)
    return data


# ── Search ──────────────────────────────────────────────────────────
async def search_tweets(client, api_key, logger, keyword, search_tab, count,
                        lang, since, until, min_likes, min_retweets):
    log = logger.log
    search_url = build_search_url(keyword, search_tab, lang, since, until, min_likes, min_retweets)
    log(f"\n[Step 2] Search: {search_url} (count={count})")

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

    tweets = []
    if response:
        content = response.get("content") or response.get("page", {}).get("content", [])
        log(f"  total content items: {len(content)}")
        for item in content:
            fields = item.get("fields", {})
            if isinstance(fields, str):
                fields = json.loads(fields)
            tweets.append({"content_type": item.get("content_type", ""), **fields})
            author = fields.get("author_username", fields.get("author_name", ""))
            text = fields.get("text", "")[:60]
            log(f"    {item.get('content_type')}: @{author} — {text} | likes={fields.get('like_count')}")

        stats = response.get("collection_stats")
        if stats:
            if isinstance(stats, str):
                stats = json.loads(stats)
            log(f"  Collection stats: {stats}")
    else:
        log(f"  Search failed: {err}")

    return tweets


# ── Parallel fetch ──────────────────────────────────────────────────
async def _fetch_one(client, api_key, logger, tweets, tweet_idx, link,
                     comment_count, label_prefix):
    """Fetch a single tweet detail. Returns (tweet_idx, data_or_error)."""
    log = logger.log
    for attempt in range(1, RETRY_COUNT + 2):
        response, err = await timed(
            logger,
            f"{label_prefix} detail+replies (attempt {attempt}/{RETRY_COUNT + 1})",
            api_browse(
                client, api_key, link,
                count=comment_count if comment_count > 0 else None,
                timeout_ms=DEFAULT_TIMEOUT_MS,
            ),
            timeout=HTTP_TIMEOUT_SEC,
        )
        if response:
            data = parse_response_content(response)
            data["search_tweet"] = tweets[tweet_idx]
            data["url"] = link
            return tweet_idx, data
        if attempt < RETRY_COUNT + 1:
            log(f"    Retrying in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    return tweet_idx, {"search_tweet": tweets[tweet_idx], "error": err}


async def fetch_details_parallel(client, api_key, logger, tweets, tweet_links, comment_count):
    log = logger.log
    comment_label = f", replies={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(tweet_links)} tweets (parallel{comment_label})")

    results = []

    for batch_start in range(0, len(tweet_links), BATCH_SIZE):
        batch = tweet_links[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(tweet_links) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"\n  Batch {batch_num}/{total_batches} ({len(batch)} tweets)")

        tasks = [
            _fetch_one(
                client, api_key, logger, tweets, tweet_idx, link,
                comment_count,
                f"    [{batch_start + i + 1}/{len(tweet_links)}]",
            )
            for i, (tweet_idx, link) in enumerate(batch)
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                log(f"    Exception: {result}")
                continue
            tweet_idx, data = result
            if "error" not in data:
                author = data["detail"].get("author_username", "N/A")
                log(f"    OK: @{author} — {len(data.get('comments', []))} replies")
            results.append(data)

    return results


# ── Sequential fetch ────────────────────────────────────────────────
async def fetch_details_sequential(client, api_key, logger, tweets, tweet_links, comment_count):
    log = logger.log
    comment_label = f", replies={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(tweet_links)} tweets (sequential{comment_label})")

    results = []

    for seq, (tweet_idx, link) in enumerate(tweet_links):
        if seq > 0:
            log(f"  (waiting {REQUEST_DELAY_SEC}s...)")
            await asyncio.sleep(REQUEST_DELAY_SEC)

        _, data = await _fetch_one(
            client, api_key, logger, tweets, tweet_idx, link,
            comment_count,
            f"[{seq + 1}/{len(tweet_links)}]",
        )

        if "error" not in data:
            author = data["detail"].get("author_username", "")
            text = data["detail"].get("text", "")[:50]
            log(f"    detail: @{author} — {text} | likes={data['detail'].get('like_count')}")
            log(f"    replies: {len(data['comments'])}")
        else:
            log(f"    Failed: {data['error']}")
        results.append(data)

    return results


def extract_tweet_links(tweets):
    links = []
    for i, tweet in enumerate(tweets):
        link = tweet.get("link") or tweet.get("url")
        if link:
            if link.startswith("/"):
                link = f"https://x.com{link}"
            links.append((i, link))
    return links


# ── Main ────────────────────────────────────────────────────────────
async def collect_by_keyword(
    keyword: str,
    search_tab: str,
    count: int,
    comment_count: int,
    parallel: bool,
    lang: str | None,
    since: str | None,
    until: str | None,
    min_likes: int | None,
    min_retweets: int | None,
):
    api_key = os.getenv("SELA_API_KEY")
    if not api_key:
        print("ERROR: SELA_API_KEY not set in .env")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"output/x_keyword_api_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log

    t_start = time.time()

    async with httpx.AsyncClient() as client:
        try:
            log("=" * 60)
            log(f"X Collect by Keyword (API) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            log(f"  keyword:      {keyword}")
            log(f"  search_tab:   {search_tab}")
            log(f"  count:        {count} tweets")
            log(f"  replies:      {comment_count} per tweet")
            log(f"  parallel:     {parallel}")
            if lang:
                log(f"  lang:         {lang}")
            if since:
                log(f"  since:        {since}")
            if until:
                log(f"  until:        {until}")
            if min_likes:
                log(f"  min_likes:    {min_likes}")
            if min_retweets:
                log(f"  min_retweets: {min_retweets}")
            log(f"  output:       {output_dir}")
            log("=" * 60)

            # ── Step 1: Search ────────────────────────────────────────
            tweets = await search_tweets(client, api_key, logger, keyword, search_tab, count,
                                         lang, since, until, min_likes, min_retweets)
            if not tweets:
                return

            save_jsonl(tweets, output_dir / "search_results.jsonl")
            log(f"  Saved {len(tweets)} tweets → {output_dir / 'search_results.jsonl'}")

            # ── Step 2: Fetch tweet details + replies ─────────────────
            tweet_links = extract_tweet_links(tweets)

            if tweet_links and parallel:
                results = await fetch_details_parallel(client, api_key, logger, tweets, tweet_links, comment_count)
            elif tweet_links:
                results = await fetch_details_sequential(client, api_key, logger, tweets, tweet_links, comment_count)
            else:
                results = []

            if results:
                output_file = output_dir / ("tweets_with_replies.jsonl" if comment_count > 0 else "tweets_with_details.jsonl")
                save_jsonl(results, output_file)
                log(f"\n  Saved {len(results)} results → {output_file}")

            # ── Summary ───────────────────────────────────────────────
            elapsed = time.time() - t_start
            total_replies = sum(len(r.get("comments", [])) for r in results) if results else 0

            log("\n" + "=" * 60)
            log("Summary:")
            log(f"  Search results: {len(tweets)} tweets")
            if results:
                ok = sum(1 for r in results if "error" not in r)
                log(f"  Details fetched: {ok}/{len(tweet_links)}")
                log(f"  Replies collected: {total_replies}")
            log(f"  Output dir: {output_dir}")
            log(f"  Elapsed: {elapsed:.1f}s")
            log("=" * 60)

            # ── Sample output ─────────────────────────────────────
            print("\n" + "=" * 60)
            print("Sample collected data")
            print("=" * 60)

            sample_results = [r for r in results if "error" not in r][:3] if results else []
            for i, r in enumerate(sample_results):
                detail = r.get("detail", {})
                comments = r.get("comments", [])
                author = detail.get("author_username", detail.get("author_name", "N/A"))
                text = detail.get("text", "N/A")
                print(f"\n[Post {i+1}] @{author}")
                print(f"  text:     {text[:120]}{'...' if len(text) > 120 else ''}")
                print(f"  likes:    {detail.get('like_count', 'N/A')}")
                print(f"  retweets: {detail.get('retweet_count', 'N/A')}")
                print(f"  replies:  {len(comments)} collected")
                for j, c in enumerate(comments[:2]):
                    c_author = c.get("author_username", c.get("author_name", "N/A"))
                    c_text = c.get("text", "N/A")
                    print(f"    reply {j+1}: @{c_author} — {c_text[:80]}{'...' if len(c_text) > 80 else ''}")
                if len(comments) > 2:
                    print(f"    ... and {len(comments) - 2} more replies")

            if not sample_results and tweets:
                print("\n(No detail results — showing search tweets)")
                for i, t in enumerate(tweets[:3]):
                    author = t.get("author_username", t.get("author_name", "N/A"))
                    text = t.get("text", "N/A")
                    print(f"  [{i+1}] @{author} — {text[:100]}{'...' if len(text) > 100 else ''}")

            print("=" * 60)

        finally:
            log(f"\nLog saved → {output_dir / 'collect.log'}")
            logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="X (Twitter) — Collect Tweets by Keyword (API)")
    parser.add_argument("--keyword", default="kpop", help="Search keyword")
    parser.add_argument("--search-tab", default="Top",
                        choices=["Top", "Latest", "People", "Media"],
                        help="Search tab (default: Top)")
    parser.add_argument("--count", type=int, default=10, help="Number of tweets to search")
    parser.add_argument("--comments", type=int, default=20,
                        help="Number of replies per tweet (0 = skip replies)")
    parser.add_argument("--parallel", action="store_true",
                        help="Use concurrent requests (fast). Default is sequential.")
    parser.add_argument("--lang", default=None, help="Language code (e.g., en, ko, ja)")
    parser.add_argument("--since", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--min-likes", type=int, default=None, help="Minimum likes filter")
    parser.add_argument("--min-retweets", type=int, default=None, help="Minimum retweets filter")
    args = parser.parse_args()

    asyncio.run(collect_by_keyword(
        args.keyword, args.search_tab, args.count, args.comments, args.parallel,
        args.lang, args.since, args.until, args.min_likes, args.min_retweets,
    ))
