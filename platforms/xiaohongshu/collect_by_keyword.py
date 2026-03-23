"""
Xiaohongshu — Collect Notes by Keyword

Flow:
  1. Initialize client
  2. Search XHS for a keyword via URL
  3. For each search result, fetch note detail + comments
  4. Save results to JSONL

Options:
  --parallel    Use parallel browse (fast, batched). Default is sequential (rate limiting & retry).
  --comments N  Number of comments per note. 0 = skip comments.

Usage:
  python platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 10
  python platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 50 --comments 30
  python platforms/xiaohongshu/collect_by_keyword.py --keyword "xiaomi" --count 50 --parallel
  python platforms/xiaohongshu/collect_by_keyword.py --keyword "xiaomi" --count 50 --parallel --comments 0

Periods: day, week, half_year, all
Sort:    comprehensive, newest, most_liked, most_commented, most_collected
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
from selanet_sdk import SelaClient, BrowseOptions, ParallelBrowseItem

from shared import create_logger, timed, save_jsonl

load_dotenv()


# ── Config ──────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_MS = 240_000   # 4 min per request
BATCH_SIZE = 3
REQUEST_DELAY_SEC = 5          # delay between sequential requests (rate limiting)
RETRY_COUNT = 1                # number of retries on failure (1 = try once more)

XHS_SEARCH_BASE = "https://www.xiaohongshu.com/search_result"


def build_search_url(keyword: str, sort: str, period: str) -> str:
    """Build XHS search URL with filters encoded as query parameters."""
    params = {
        "keyword": keyword,
        # "sort": sort,
        # "publish_time": period,
        "source": "web_explore_feed",
    }
    return f"{XHS_SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


def parse_response_content(response, notes_list):
    """Parse a browse response into a dict with detail and comments."""
    data = {"search_note": None, "detail": {}, "comments": []}
    for item in response.page.content:
        fields = json.loads(item.fields_json)
        if item.content_type == "note_detail":
            data["detail"] = fields
        elif item.content_type == "comment":
            data["comments"].append(fields)
    return data


# ── Search ──────────────────────────────────────────────────────────
async def search_notes(client, logger, keyword, sort, period, count):
    """Search XHS for notes matching a keyword. Returns list of note dicts."""
    log = logger.log
    search_url = build_search_url(keyword, sort, period)
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
            timeout=360,
        )
        if search_response:
            break
        if attempt < RETRY_COUNT + 1:
            log(f"  Retrying search in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    notes = []
    if search_response and search_response.page:
        log(f"  total content items: {len(search_response.page.content)}")
        for item in search_response.page.content:
            fields = json.loads(item.fields_json)
            notes.append({"content_type": item.content_type, **fields})
            log(f"    {item.content_type}: {fields.get('title', '')[:60]} | likes={fields.get('like_count')}")

        if search_response.collection_stats:
            stats = json.loads(search_response.collection_stats)
            log(f"  Collection stats: {stats}")
    else:
        log(f"  Search failed: {err}")

    return notes


# ── Parallel fetch ──────────────────────────────────────────────────
async def fetch_details_parallel(client, logger, notes, note_links, comment_count):
    """Fetch note details in parallel batches with retry."""
    log = logger.log
    comment_label = f", comments={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(note_links)} notes (parallel{comment_label})")

    items = [
        ParallelBrowseItem(
            url=link,
            options=BrowseOptions(
                count=comment_count if comment_count > 0 else None,
                timeout_ms=DEFAULT_TIMEOUT_MS,
            ),
        )
        for _, link in note_links
    ]

    results = []

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE

        batch_results, err = await timed(
            logger,
            f"browse_parallel_collect (batch {batch_num}/{total_batches}, {len(batch)} notes)",
            client.browse_parallel_collect(batch, max_concurrent_per_agent=BATCH_SIZE),
            timeout=360,
        )

        failed_indices = []
        if batch_results:
            for r in sorted(batch_results, key=lambda r: r.index):
                note_idx, link = note_links[batch_start + r.index]
                if r.error:
                    log(f"    [{batch_start + r.index + 1}/{len(note_links)}] FAIL: {r.error}")
                    failed_indices.append((batch_start + r.index, note_idx, link))
                else:
                    data = parse_response_content(r.response, notes)
                    data["search_note"] = notes[note_idx]
                    data["url"] = link
                    data["elapsed_ms"] = r.elapsed_ms
                    title = r.response.page.metadata.title or "N/A"
                    log(f"    [{batch_start + r.index + 1}/{len(note_links)}] {title[:50]} — {len(data['comments'])} comments ({r.elapsed_ms}ms)")
                    results.append(data)
        else:
            log(f"    Batch {batch_num} failed: {err}")
            failed_indices = [
                (batch_start + i, note_links[batch_start + i][0], note_links[batch_start + i][1])
                for i in range(len(batch))
            ]

        # Retry failed items
        failed_indices = await _retry_parallel(client, logger, notes, note_links, failed_indices, comment_count, results)

        for global_idx, note_idx, link in failed_indices:
            results.append({"search_note": notes[note_idx], "error": "failed after retries"})

    return results


async def _retry_parallel(client, logger, notes, note_links, failed_indices, comment_count, results):
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
                ),
            )
            for _, _, link in failed_indices
        ]
        retry_results, retry_err = await timed(
            logger,
            f"browse_parallel_collect (retry, {len(retry_items)} notes)",
            client.browse_parallel_collect(retry_items, max_concurrent_per_agent=BATCH_SIZE),
            timeout=360,
        )
        if retry_results:
            for r in sorted(retry_results, key=lambda r: r.index):
                global_idx, note_idx, link = failed_indices[r.index]
                if r.error:
                    log(f"    [{global_idx + 1}/{len(note_links)}] RETRY FAIL: {r.error}")
                    still_failed.append((global_idx, note_idx, link))
                else:
                    data = parse_response_content(r.response, notes)
                    data["search_note"] = notes[note_idx]
                    data["url"] = link
                    data["elapsed_ms"] = r.elapsed_ms
                    title = r.response.page.metadata.title or "N/A"
                    log(f"    [{global_idx + 1}/{len(note_links)}] RETRY OK: {title[:50]} — {len(data['comments'])} comments ({r.elapsed_ms}ms)")
                    results.append(data)
        else:
            log(f"    Retry batch failed: {retry_err}")
            still_failed = failed_indices
        failed_indices = still_failed
    return failed_indices


# ── Sequential fetch ────────────────────────────────────────────────
async def fetch_details_sequential(client, logger, notes, note_links, comment_count):
    """Fetch note details one by one with rate limiting and retry."""
    log = logger.log
    comment_label = f", comments={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(note_links)} notes (sequential{comment_label})")

    results = []

    for seq, (note_idx, link) in enumerate(note_links):
        if seq > 0:
            log(f"  (waiting {REQUEST_DELAY_SEC}s...)")
            await asyncio.sleep(REQUEST_DELAY_SEC)

        detail_response = None
        last_err = None

        for attempt in range(1, RETRY_COUNT + 2):
            detail_response, last_err = await timed(
                logger,
                f"[{seq + 1}/{len(note_links)}] detail+comments (attempt {attempt}/{RETRY_COUNT + 1})",
                client.browse(
                    url=link,
                    options=BrowseOptions(
                        count=comment_count if comment_count > 0 else None,
                        timeout_ms=DEFAULT_TIMEOUT_MS,
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
            data = parse_response_content(detail_response, notes)
            data["search_note"] = notes[note_idx]
            log(f"    detail: {data['detail'].get('title', '')[:50]} | likes={data['detail'].get('like_count')}")
            log(f"    comments: {len(data['comments'])}")
            results.append(data)
        else:
            log(f"    Failed after {RETRY_COUNT + 1} attempts: {last_err}")
            results.append({"search_note": notes[note_idx], "error": last_err})

    return results


# ── Main ────────────────────────────────────────────────────────────
async def collect_by_keyword(
    keyword: str,
    period: str,
    sort: str,
    count: int,
    comment_count: int,
    parallel: bool,
):
    api_key = os.getenv("SELA_API_KEY")
    if not api_key:
        print("ERROR: SELA_API_KEY not set in .env")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"output/xhs_keyword_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log
    client = None

    t_start = time.time()

    try:
        log("=" * 60)
        log(f"XHS Collect by Keyword — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"  keyword:  {keyword}")
        log(f"  period:   {period}")
        log(f"  sort:     {sort}")
        log(f"  count:    {count} notes")
        log(f"  comments: {comment_count} per note")
        log(f"  parallel: {parallel}")
        log(f"  output:   {output_dir}")
        log("=" * 60)

        # ── Step 1: Initialize client ─────────────────────────────
        log("\n[Step 1] Initialize client")
        client, err = await timed(logger, "SelaClient.with_api_key", SelaClient.with_api_key(api_key), timeout=30)
        if not client:
            log(f"  FATAL: Cannot create client: {err}")
            return

        # ── Step 2: Search ─────────────────────────────────────────
        notes = await search_notes(client, logger, keyword, sort, period, count)
        if not notes:
            return

        save_jsonl(notes, output_dir / "search_results.jsonl")
        log(f"  Saved {len(notes)} notes → {output_dir / 'search_results.jsonl'}")

        # ── Step 3: Fetch note details + comments ──────────────────
        note_links = [(i, note["link"]) for i, note in enumerate(notes) if note.get("link")]

        if note_links and parallel:
            results = await fetch_details_parallel(client, logger, notes, note_links, comment_count)
        elif note_links:
            results = await fetch_details_sequential(client, logger, notes, note_links, comment_count)
        else:
            results = []

        if results:
            output_file = output_dir / ("notes_with_comments.jsonl" if comment_count > 0 else "notes_with_details.jsonl")
            save_jsonl(results, output_file)
            log(f"\n  Saved {len(results)} results → {output_file}")

        # ── Summary ────────────────────────────────────────────────
        elapsed = time.time() - t_start
        total_comments = sum(len(r.get("comments", [])) for r in results) if results else 0

        log("\n" + "=" * 60)
        log("Summary:")
        log(f"  Search results: {len(notes)} notes")
        if results:
            ok = sum(1 for r in results if "error" not in r)
            log(f"  Details fetched: {ok}/{len(note_links)}")
            log(f"  Comments collected: {total_comments}")
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
    parser = argparse.ArgumentParser(description="XHS — Collect Notes by Keyword")
    parser.add_argument("--keyword", default="kpop", help="Search keyword")
    parser.add_argument("--period", default="all",
                        choices=["all", "day", "week", "half_year"],
                        help="Publish time filter")
    parser.add_argument("--sort", default="most_liked",
                        choices=["comprehensive", "newest", "most_liked", "most_commented", "most_collected"],
                        help="Sort order")
    parser.add_argument("--count", type=int, default=10, help="Number of notes to search")
    parser.add_argument("--comments", type=int, default=20,
                        help="Number of comments per note (0 = skip comments)")
    parser.add_argument("--parallel", action="store_true",
                        help="Use parallel browse (fast, batched). Default is sequential.")
    args = parser.parse_args()

    asyncio.run(collect_by_keyword(args.keyword, args.period, args.sort, args.count, args.comments, args.parallel))
