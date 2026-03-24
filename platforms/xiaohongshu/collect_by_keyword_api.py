"""
Xiaohongshu — Collect Notes by Keyword (REST API version, no SDK)

Same functionality as collect_by_keyword.py but uses the Selanet REST API
directly via httpx instead of the selanet_sdk.

API endpoint: POST https://api.selanet.ai/v1/browse
Auth: Bearer <SELA_API_KEY>

Flow:
  1. Search XHS for a keyword via API
  2. For each search result, fetch note detail + comments
  3. Save results to JSONL

Options:
  --parallel    Use asyncio.gather for concurrent requests. Default is sequential.
  --comments N  Number of comments per note. 0 = skip comments.

Usage:
  uv run platforms/xiaohongshu/collect_by_keyword_api.py --keyword "kpop" --count 10
  uv run platforms/xiaohongshu/collect_by_keyword_api.py --keyword "xiaomi" --count 50 --parallel
  uv run platforms/xiaohongshu/collect_by_keyword_api.py --keyword "xiaomi" --count 50 --parallel --comments 0

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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from dotenv import load_dotenv

from shared import create_logger, timed, save_jsonl

load_dotenv()


# ── Config ──────────────────────────────────────────────────────────
API_BASE_URL = "https://api.selanet.ai/v1"
DEFAULT_TIMEOUT_MS = 300_000   # 5 min per request
HTTP_TIMEOUT_SEC = 600         # 10 min httpx timeout
BATCH_SIZE = 9
REQUEST_DELAY_SEC = 5
RETRY_COUNT = 1

XHS_SEARCH_BASE = "https://www.xiaohongshu.com/search_result"


def build_search_url(keyword: str, sort: str, period: str) -> str:
    params = {
        "keyword": keyword,
        "source": "web_explore_feed",
    }
    return f"{XHS_SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


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
    """Parse an API browse response into a dict with detail and comments."""
    data = {"search_note": None, "detail": {}, "comments": []}
    content = response.get("content") or response.get("page", {}).get("content", [])
    for item in content:
        fields = item.get("fields", {})
        if isinstance(fields, str):
            fields = json.loads(fields)
        content_type = item.get("content_type", "")
        if content_type == "note_detail":
            data["detail"] = fields
        elif content_type == "comment":
            data["comments"].append(fields)
    return data


# ── Search ──────────────────────────────────────────────────────────
async def search_notes(client, api_key, logger, keyword, sort, period, count):
    log = logger.log
    search_url = build_search_url(keyword, sort, period)
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

    notes = []
    if response:
        content = response.get("content") or response.get("page", {}).get("content", [])
        log(f"  total content items: {len(content)}")
        for item in content:
            fields = item.get("fields", {})
            if isinstance(fields, str):
                fields = json.loads(fields)
            notes.append({"content_type": item.get("content_type", ""), **fields})
            log(f"    {item.get('content_type')}: {fields.get('title', '')[:60]} | likes={fields.get('like_count')}")

        stats = response.get("collection_stats")
        if stats:
            if isinstance(stats, str):
                stats = json.loads(stats)
            log(f"  Collection stats: {stats}")
    else:
        log(f"  Search failed: {err}")

    return notes


# ── Parallel fetch ──────────────────────────────────────────────────
async def _fetch_one(client, api_key, logger, notes, note_idx, link,
                     comment_count, label_prefix):
    """Fetch a single note detail. Returns (note_idx, data_or_error)."""
    log = logger.log
    for attempt in range(1, RETRY_COUNT + 2):
        response, err = await timed(
            logger,
            f"{label_prefix} detail+comments (attempt {attempt}/{RETRY_COUNT + 1})",
            api_browse(
                client, api_key, link,
                count=comment_count if comment_count > 0 else None,
                timeout_ms=DEFAULT_TIMEOUT_MS,
            ),
            timeout=HTTP_TIMEOUT_SEC,
        )
        if response:
            data = parse_response_content(response)
            data["search_note"] = notes[note_idx]
            data["url"] = link
            return note_idx, data
        if attempt < RETRY_COUNT + 1:
            log(f"    Retrying in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    return note_idx, {"search_note": notes[note_idx], "error": err}


async def fetch_details_parallel(client, api_key, logger, notes, note_links, comment_count):
    log = logger.log
    comment_label = f", comments={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(note_links)} notes (parallel{comment_label})")

    results = []

    for batch_start in range(0, len(note_links), BATCH_SIZE):
        batch = note_links[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(note_links) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"\n  Batch {batch_num}/{total_batches} ({len(batch)} notes)")

        tasks = [
            _fetch_one(
                client, api_key, logger, notes, note_idx, link,
                comment_count,
                f"    [{batch_start + i + 1}/{len(note_links)}]",
            )
            for i, (note_idx, link) in enumerate(batch)
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                log(f"    Exception: {result}")
                continue
            note_idx, data = result
            if "error" not in data:
                title = data["detail"].get("title", "N/A")[:50]
                log(f"    OK: {title} — {len(data.get('comments', []))} comments")
            results.append(data)

    return results


# ── Sequential fetch ────────────────────────────────────────────────
async def fetch_details_sequential(client, api_key, logger, notes, note_links, comment_count):
    log = logger.log
    comment_label = f", comments={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(note_links)} notes (sequential{comment_label})")

    results = []

    for seq, (note_idx, link) in enumerate(note_links):
        if seq > 0:
            log(f"  (waiting {REQUEST_DELAY_SEC}s...)")
            await asyncio.sleep(REQUEST_DELAY_SEC)

        _, data = await _fetch_one(
            client, api_key, logger, notes, note_idx, link,
            comment_count,
            f"[{seq + 1}/{len(note_links)}]",
        )

        if "error" not in data:
            log(f"    detail: {data['detail'].get('title', '')[:50]} | likes={data['detail'].get('like_count')}")
            log(f"    comments: {len(data['comments'])}")
        else:
            log(f"    Failed: {data['error']}")
        results.append(data)

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
    output_dir = Path(f"output/xhs_keyword_api_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log

    t_start = time.time()

    async with httpx.AsyncClient() as client:
        try:
            log("=" * 60)
            log(f"XHS Collect by Keyword (API) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            log(f"  keyword:  {keyword}")
            log(f"  period:   {period}")
            log(f"  sort:     {sort}")
            log(f"  count:    {count} notes")
            log(f"  comments: {comment_count} per note")
            log(f"  parallel: {parallel}")
            log(f"  output:   {output_dir}")
            log("=" * 60)

            # ── Step 1: Search ────────────────────────────────────────
            notes = await search_notes(client, api_key, logger, keyword, sort, period, count)
            if not notes:
                return

            save_jsonl(notes, output_dir / "search_results.jsonl")
            log(f"  Saved {len(notes)} notes → {output_dir / 'search_results.jsonl'}")

            # ── Step 2: Fetch note details + comments ─────────────────
            note_links = [(i, note["link"]) for i, note in enumerate(notes) if note.get("link")]

            if note_links and parallel:
                results = await fetch_details_parallel(client, api_key, logger, notes, note_links, comment_count)
            elif note_links:
                results = await fetch_details_sequential(client, api_key, logger, notes, note_links, comment_count)
            else:
                results = []

            if results:
                output_file = output_dir / ("notes_with_comments.jsonl" if comment_count > 0 else "notes_with_details.jsonl")
                save_jsonl(results, output_file)
                log(f"\n  Saved {len(results)} results → {output_file}")

            # ── Summary ───────────────────────────────────────────────
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

            # ── Sample output ─────────────────────────────────────
            print("\n" + "=" * 60)
            print("Sample collected data")
            print("=" * 60)

            sample_results = [r for r in results if "error" not in r][:3] if results else []
            for i, r in enumerate(sample_results):
                detail = r.get("detail", {})
                comments = r.get("comments", [])
                title = detail.get("title", "N/A")
                text = detail.get("text", "N/A")
                author = detail.get("author_name", detail.get("author_nickname", "N/A"))
                print(f"\n[Post {i+1}] {title}")
                print(f"  author:   {author}")
                print(f"  text:     {text[:120]}{'...' if len(text) > 120 else ''}")
                print(f"  likes:    {detail.get('like_count', 'N/A')}")
                print(f"  collects: {detail.get('collect_count', 'N/A')}")
                print(f"  comments: {len(comments)} collected")
                for j, c in enumerate(comments[:2]):
                    c_author = c.get("author_name", c.get("author_nickname", "N/A"))
                    c_text = c.get("text", "N/A")
                    print(f"    comment {j+1}: {c_author} — {c_text[:80]}{'...' if len(c_text) > 80 else ''}")
                if len(comments) > 2:
                    print(f"    ... and {len(comments) - 2} more comments")

            if not sample_results and notes:
                print("\n(No detail results — showing search notes)")
                for i, n in enumerate(notes[:3]):
                    title = n.get("title", "N/A")
                    print(f"  [{i+1}] {title[:100]} | likes={n.get('like_count', 'N/A')}")

            print("=" * 60)

        finally:
            log(f"\nLog saved → {output_dir / 'collect.log'}")
            logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XHS — Collect Notes by Keyword (API)")
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
                        help="Use concurrent requests (fast). Default is sequential.")
    args = parser.parse_args()

    asyncio.run(collect_by_keyword(args.keyword, args.period, args.sort, args.count, args.comments, args.parallel))
