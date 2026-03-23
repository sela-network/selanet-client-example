"""
X (Twitter) — Collect Tweets by Keyword

Flow:
  1. Initialize client
  2. Search X for a keyword using XParams
  3. For each tweet, fetch tweet detail + replies (comments)
  4. Save results to JSONL

Options:
  --parallel    Use parallel browse (fast, batched). Default is sequential (rate limiting & retry).
  --comments N  Number of replies per tweet. 0 = skip replies.

Usage:
  python platforms/x/collect_by_keyword.py --keyword "kpop" --count 10
  python platforms/x/collect_by_keyword.py --keyword "AI" --count 20 --comments 50
  python platforms/x/collect_by_keyword.py --keyword "python" --count 50 --parallel
  python platforms/x/collect_by_keyword.py --keyword "python" --count 50 --parallel --comments 0

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

# Ensure project root is importable when run from any directory
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from selanet_sdk import SelaClient, BrowseOptions, XParams, ParallelBrowseItem

from shared import create_logger, timed, save_jsonl

load_dotenv()


# ── Config ──────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_MS = 240_000   # 4 min per request
BATCH_SIZE = 3
REQUEST_DELAY_SEC = 5          # delay between sequential requests (rate limiting)
RETRY_COUNT = 1                # number of retries on failure (1 = try once more)

X_BASE_URL = "https://x.com"


def parse_response_content(response):
    """Parse a browse response into a dict with tweet detail and replies."""
    data = {"search_tweet": None, "detail": {}, "comments": []}
    for item in response.page.content:
        fields = json.loads(item.fields_json)
        if item.content_type in ("tweet_detail", "tweet"):
            data["detail"] = fields
        elif item.content_type in ("comment", "reply"):
            data["comments"].append(fields)
    return data


# ── Search ──────────────────────────────────────────────────────────
async def search_tweets(client, logger, keyword, search_tab, count, lang, since, until,
                        min_likes, min_retweets):
    """Search X for tweets matching a keyword. Returns list of tweet dicts."""
    log = logger.log
    log(f"\n[Step 2] Search X: keyword=\"{keyword}\" tab={search_tab} count={count}")

    x_params = XParams(
        feature="search",
        query=keyword,
        search_tab=search_tab,
        lang=lang if lang else None,
        since=since if since else None,
        until=until if until else None,
        min_likes=min_likes if min_likes else None,
        min_retweets=min_retweets if min_retweets else None,
    )

    search_response = None
    for attempt in range(1, RETRY_COUNT + 2):
        search_response, err = await timed(
            logger,
            f"client.browse (search, count={count}, attempt {attempt}/{RETRY_COUNT + 1})",
            client.browse(
                url=X_BASE_URL,
                options=BrowseOptions(
                    count=count,
                    timeout_ms=DEFAULT_TIMEOUT_MS,
                    x_params=x_params,
                ),
            ),
            timeout=360,
        )
        if search_response:
            break
        if attempt < RETRY_COUNT + 1:
            log(f"  Retrying search in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    tweets = []
    if search_response and search_response.page:
        log(f"  total content items: {len(search_response.page.content)}")
        for item in search_response.page.content:
            fields = json.loads(item.fields_json)
            tweets.append({"content_type": item.content_type, **fields})
            author = fields.get("author_username", fields.get("author_name", ""))
            text = fields.get("text", "")[:60]
            log(f"    {item.content_type}: @{author} — {text} | likes={fields.get('like_count')}")

        if search_response.collection_stats:
            stats = json.loads(search_response.collection_stats)
            log(f"  Collection stats: {stats}")
    else:
        log(f"  Search failed: {err}")

    return tweets


# ── Parallel fetch ──────────────────────────────────────────────────
async def fetch_details_parallel(client, logger, tweets, tweet_links, comment_count):
    """Fetch tweet details in parallel batches with retry."""
    log = logger.log
    comment_label = f", replies={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(tweet_links)} tweets (parallel{comment_label})")

    items = [
        ParallelBrowseItem(
            url=link,
            options=BrowseOptions(
                count=comment_count if comment_count > 0 else None,
                timeout_ms=DEFAULT_TIMEOUT_MS,
                x_params=XParams(feature="tweet_detail", tweet_id=tweet_id),
            ),
        )
        for _, link, tweet_id in tweet_links
    ]

    results = []

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

        batch_results, err = await timed(
            logger,
            f"browse_parallel_collect (batch {batch_num}/{total_batches}, {len(batch)} tweets)",
            client.browse_parallel_collect(batch, max_concurrent_per_agent=BATCH_SIZE),
            timeout=360,
        )

        failed_indices = []
        if batch_results:
            for r in sorted(batch_results, key=lambda r: r.index):
                tweet_idx, link, tweet_id = tweet_links[batch_start + r.index]
                if r.error:
                    log(f"    [{batch_start + r.index + 1}/{len(tweet_links)}] FAIL: {r.error}")
                    failed_indices.append((batch_start + r.index, tweet_idx, link, tweet_id))
                else:
                    data = parse_response_content(r.response)
                    data["search_tweet"] = tweets[tweet_idx]
                    data["url"] = link
                    data["elapsed_ms"] = r.elapsed_ms
                    author = data["detail"].get("author_username", "N/A")
                    log(f"    [{batch_start + r.index + 1}/{len(tweet_links)}] @{author} — {len(data['comments'])} replies ({r.elapsed_ms}ms)")
                    results.append(data)
        else:
            log(f"    Batch {batch_num} failed: {err}")
            failed_indices = [
                (batch_start + i, tweet_links[batch_start + i][0], tweet_links[batch_start + i][1], tweet_links[batch_start + i][2])
                for i in range(len(batch))
            ]

        # Retry failed items
        failed_indices = await _retry_parallel(client, logger, tweets, tweet_links, failed_indices, comment_count, results)

        for global_idx, tweet_idx, link, tweet_id in failed_indices:
            results.append({"search_tweet": tweets[tweet_idx], "error": "failed after retries"})

    return results


async def _retry_parallel(client, logger, tweets, tweet_links, failed_indices, comment_count, results):
    """Retry failed parallel browse items."""
    log = logger.log
    for retry_round in range(RETRY_COUNT):
        if not failed_indices:
            break
        log(f"    Retrying {len(failed_indices)} failed items (retry {retry_round + 1}/{RETRY_COUNT})...")
        still_failed = []
        retry_items = [
            ParallelBrowseItem(
                url=link,
                options=BrowseOptions(
                    count=comment_count if comment_count > 0 else None,
                    timeout_ms=DEFAULT_TIMEOUT_MS,
                    x_params=XParams(feature="tweet_detail", tweet_id=tweet_id),
                ),
            )
            for _, _, link, tweet_id in failed_indices
        ]
        retry_results, retry_err = await timed(
            logger,
            f"browse_parallel_collect (retry, {len(retry_items)} tweets)",
            client.browse_parallel_collect(retry_items, max_concurrent_per_agent=BATCH_SIZE),
            timeout=360,
        )
        if retry_results:
            for r in sorted(retry_results, key=lambda r: r.index):
                global_idx, tweet_idx, link, tweet_id = failed_indices[r.index]
                if r.error:
                    log(f"    [{global_idx + 1}/{len(tweet_links)}] RETRY FAIL: {r.error}")
                    still_failed.append((global_idx, tweet_idx, link, tweet_id))
                else:
                    data = parse_response_content(r.response)
                    data["search_tweet"] = tweets[tweet_idx]
                    data["url"] = link
                    data["elapsed_ms"] = r.elapsed_ms
                    author = data["detail"].get("author_username", "N/A")
                    log(f"    [{global_idx + 1}/{len(tweet_links)}] RETRY OK: @{author} — {len(data['comments'])} replies ({r.elapsed_ms}ms)")
                    results.append(data)
        else:
            log(f"    Retry batch failed: {retry_err}")
            still_failed = failed_indices
        failed_indices = still_failed
    return failed_indices


# ── Sequential fetch ────────────────────────────────────────────────
async def fetch_details_sequential(client, logger, tweets, tweet_links, comment_count):
    """Fetch tweet details one by one with rate limiting and retry."""
    log = logger.log
    comment_label = f", replies={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(tweet_links)} tweets (sequential{comment_label})")

    results = []

    for seq, (tweet_idx, link, tweet_id) in enumerate(tweet_links):
        if seq > 0:
            log(f"  (waiting {REQUEST_DELAY_SEC}s...)")
            await asyncio.sleep(REQUEST_DELAY_SEC)

        detail_response = None
        last_err = None

        for attempt in range(1, RETRY_COUNT + 2):
            detail_response, last_err = await timed(
                logger,
                f"[{seq + 1}/{len(tweet_links)}] detail+replies (attempt {attempt}/{RETRY_COUNT + 1})",
                client.browse(
                    url=link,
                    options=BrowseOptions(
                        count=comment_count if comment_count > 0 else None,
                        timeout_ms=DEFAULT_TIMEOUT_MS,
                        x_params=XParams(feature="tweet_detail", tweet_id=tweet_id),
                    ),
                ),
                timeout=360,
            )
            if detail_response:
                break
            if attempt < RETRY_COUNT + 1:
                log(f"    Retrying in {REQUEST_DELAY_SEC}s...")
                await asyncio.sleep(REQUEST_DELAY_SEC)

        if detail_response and detail_response.page:
            data = parse_response_content(detail_response)
            data["search_tweet"] = tweets[tweet_idx]
            author = data["detail"].get("author_username", "")
            text = data["detail"].get("text", "")[:50]
            log(f"    detail: @{author} — {text} | likes={data['detail'].get('like_count')}")
            log(f"    replies: {len(data['comments'])}")
            results.append(data)
        else:
            log(f"    Failed after {RETRY_COUNT + 1} attempts: {last_err}")
            results.append({"search_tweet": tweets[tweet_idx], "error": last_err})

    return results


def extract_tweet_links(tweets):
    """Extract (index, url, tweet_id) tuples from search results."""
    links = []
    for i, tweet in enumerate(tweets):
        link = tweet.get("link") or tweet.get("url")
        tweet_id = tweet.get("tweet_id") or tweet.get("id")
        if link:
            # Extract tweet_id from URL if not in fields: https://x.com/user/status/123456
            if not tweet_id and "/status/" in link:
                tweet_id = link.split("/status/")[-1].split("?")[0].split("/")[0]
            links.append((i, link, tweet_id))
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
    output_dir = Path(f"output/x_keyword_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log
    client = None

    t_start = time.time()

    try:
        log("=" * 60)
        log(f"X Collect by Keyword — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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

        # ── Step 1: Initialize client ─────────────────────────────
        log("\n[Step 1] Initialize client")
        client, err = await timed(logger, "SelaClient.with_api_key", SelaClient.with_api_key(api_key), timeout=30)
        if not client:
            log(f"  FATAL: Cannot create client: {err}")
            return

        # ── Step 2: Search ─────────────────────────────────────────
        tweets = await search_tweets(client, logger, keyword, search_tab, count, lang, since, until,
                                     min_likes, min_retweets)
        if not tweets:
            return

        save_jsonl(tweets, output_dir / "search_results.jsonl")
        log(f"  Saved {len(tweets)} tweets → {output_dir / 'search_results.jsonl'}")

        # ── Step 3: Fetch tweet details + replies ──────────────────
        tweet_links = extract_tweet_links(tweets)

        if tweet_links and parallel:
            results = await fetch_details_parallel(client, logger, tweets, tweet_links, comment_count)
        elif tweet_links:
            results = await fetch_details_sequential(client, logger, tweets, tweet_links, comment_count)
        else:
            results = []

        if results:
            output_file = output_dir / ("tweets_with_replies.jsonl" if comment_count > 0 else "tweets_with_details.jsonl")
            save_jsonl(results, output_file)
            log(f"\n  Saved {len(results)} results → {output_file}")

        # ── Summary ────────────────────────────────────────────────
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

    finally:
        if client:
            log("\n[Cleanup] Shutting down...")
            await timed(logger, "client.shutdown()", client.shutdown(timeout_ms=15_000), timeout=30)
        log(f"\nLog saved → {output_dir / 'collect.log'}")
        logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="X (Twitter) — Collect Tweets by Keyword")
    parser.add_argument("--keyword", default="kpop", help="Search keyword")
    parser.add_argument("--search-tab", default="Top",
                        choices=["Top", "Latest", "People", "Media"],
                        help="Search tab (default: Top)")
    parser.add_argument("--count", type=int, default=10, help="Number of tweets to search")
    parser.add_argument("--comments", type=int, default=20,
                        help="Number of replies per tweet (0 = skip replies)")
    parser.add_argument("--parallel", action="store_true",
                        help="Use parallel browse (fast, batched). Default is sequential.")
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
