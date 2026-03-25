"""
YouTube — Collect Videos by Keyword (REST API version, no SDK)

Same pattern as X collect_by_keyword_api.py but targets YouTube.

API endpoint: POST https://api.selanet.ai/v1/browse
Auth: Bearer <SELA_API_KEY>

Flow:
  1. Search YouTube for a keyword via API
  2. For each video, fetch video detail (video_info)
  3. Fetch comments separately (youtube_params feature="comments")
  4. Save results to JSONL

Options:
  --parallel    Use asyncio.gather for concurrent requests. Default is sequential.
  --comments N  Number of comments per video. 0 = skip comments.

Usage:
  uv run platforms/youtube/collect_by_keyword_api.py --keyword "kpop" --count 10 --comments 20
  uv run platforms/youtube/collect_by_keyword_api.py --keyword "AI" --count 5 --comments 10 --parallel
  uv run platforms/youtube/collect_by_keyword_api.py --keyword "python tutorial" --count 10 --sort "Upload date"

Sort options: Relevance (default), Upload date, View count, Rating

Content types returned by Selanet API:
  search  → video (list), short (list)
  watch   → video_info (single)
  comments → comment (list)
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
DEFAULT_TIMEOUT_MS = 300_000   # 5 min per request (YouTube pages can be heavy)
HTTP_TIMEOUT_SEC = 600         # 10 min httpx timeout
BATCH_SIZE = 3
REQUEST_DELAY_SEC = 5
RETRY_COUNT = 1

YT_SEARCH_BASE = "https://www.youtube.com/results"

SORT_MAP = {
    "Relevance": None,         # default, no sp param needed
    "Upload date": "CAI=",     # urlencode will encode = → %3D
    "View count": "CAM=",
    "Rating": "CAE=",
}


def build_search_url(keyword: str, sort: str) -> str:
    """Build a YouTube search URL with optional sort parameter."""
    params = {"search_query": keyword}
    sp = SORT_MAP.get(sort)
    if sp:
        params["sp"] = sp
    return f"{YT_SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


# ── API helpers ─────────────────────────────────────────────────────
def _headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


async def api_browse(client: httpx.AsyncClient, api_key: str, url: str | None = None,
                     count: int | None = None, parse_only: bool = False,
                     timeout_ms: int = DEFAULT_TIMEOUT_MS,
                     youtube_params: dict | None = None,
                     session_id: str | None = None) -> dict:
    """Call POST /v1/browse and return the JSON response.

    The API requires exactly ONE of url or youtube_params (not both).
    Pass session_id to reuse an existing browser session.
    """
    body: dict = {"timeout_ms": timeout_ms}
    if youtube_params:
        body["youtube_params"] = youtube_params
    elif url:
        body["url"] = url
    if count is not None:
        body["count"] = count
    if parse_only:
        body["parse_only"] = parse_only
    if session_id:
        body["session_id"] = session_id

    resp = await client.post(
        f"{API_BASE_URL}/browse",
        headers=_headers(api_key),
        json=body,
        timeout=HTTP_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


def parse_response_content(response: dict):
    """Parse an API browse response into a dict with video detail and comments.

    Content types from Selanet YouTube schema:
      - video_info: video detail (single)
      - comment: user comment (list)
    """
    data = {"search_video": None, "detail": {}, "comments": []}
    content = response.get("content") or response.get("page", {}).get("content", [])
    for item in content:
        fields = item.get("fields", {})
        if isinstance(fields, str):
            fields = json.loads(fields)
        content_type = item.get("content_type", "")
        if content_type == "video_info":
            data["detail"] = fields
        elif content_type == "comment":
            data["comments"].append(fields)
    return data


# ── Search ──────────────────────────────────────────────────────────
async def search_videos(client, api_key, logger, keyword, sort, count):
    log = logger.log
    search_url = build_search_url(keyword, sort)
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

    videos = []
    if response:
        content = response.get("content") or response.get("page", {}).get("content", [])
        log(f"  total content items: {len(content)}")
        for item in content:
            fields = item.get("fields", {})
            if isinstance(fields, str):
                fields = json.loads(fields)
            videos.append({"content_type": item.get("content_type", ""), **fields})
            ct = item.get("content_type", "")
            title = fields.get("title", "")[:60]
            channel = fields.get("channel_name", "")
            views = fields.get("view_count", "N/A")
            log(f"    {ct}: {channel} — {title} | views={views}")

        stats = response.get("collection_stats")
        if stats:
            if isinstance(stats, str):
                stats = json.loads(stats)
            log(f"  Collection stats: {stats}")
    else:
        log(f"  Search failed: {err}")

    return videos


# ── Parallel fetch ──────────────────────────────────────────────────
async def _fetch_one(client, api_key, logger, videos, video_idx, link,
                     comment_count, label_prefix):
    """Fetch a single video detail + comments (separate calls).

    YouTube requires separate API calls for video_info (watch) and comments
    because comments are lazy-loaded via infinite scroll.
    """
    log = logger.log

    # ── 1) Fetch video detail (watch page_type → video_info) ──
    detail_response = None
    for attempt in range(1, RETRY_COUNT + 2):
        detail_response, err = await timed(
            logger,
            f"{label_prefix} detail (attempt {attempt}/{RETRY_COUNT + 1})",
            api_browse(client, api_key, link, timeout_ms=DEFAULT_TIMEOUT_MS),
            timeout=HTTP_TIMEOUT_SEC,
        )
        if detail_response:
            break
        if attempt < RETRY_COUNT + 1:
            log(f"    Retrying in {REQUEST_DELAY_SEC}s...")
            await asyncio.sleep(REQUEST_DELAY_SEC)

    if not detail_response:
        return video_idx, {"search_video": videos[video_idx], "error": err}

    data = parse_response_content(detail_response)
    data["search_video"] = videos[video_idx]
    data["url"] = link

    # ── 2) Fetch comments (youtube_params feature="comments") ──
    if comment_count > 0:
        for attempt in range(1, RETRY_COUNT + 2):
            comment_response, c_err = await timed(
                logger,
                f"{label_prefix} comments (attempt {attempt}/{RETRY_COUNT + 1})",
                api_browse(
                    client, api_key,
                    count=comment_count,
                    timeout_ms=DEFAULT_TIMEOUT_MS,
                    youtube_params={"feature": "comments", "url": link},
                ),
                timeout=HTTP_TIMEOUT_SEC,
            )
            if comment_response:
                comment_data = parse_response_content(comment_response)
                data["comments"] = comment_data["comments"]
                log(f"    comments fetched: {len(comment_data['comments'])}")
                break
            if attempt < RETRY_COUNT + 1:
                log(f"    Retrying comments in {REQUEST_DELAY_SEC}s...")
                await asyncio.sleep(REQUEST_DELAY_SEC)

    return video_idx, data


async def fetch_details_parallel(client, api_key, logger, videos, video_links, comment_count):
    log = logger.log
    comment_label = f", comments={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(video_links)} videos (parallel{comment_label})")

    results = []

    for batch_start in range(0, len(video_links), BATCH_SIZE):
        batch = video_links[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(video_links) + BATCH_SIZE - 1) // BATCH_SIZE
        log(f"\n  Batch {batch_num}/{total_batches} ({len(batch)} videos)")

        tasks = [
            _fetch_one(
                client, api_key, logger, videos, video_idx, link,
                comment_count,
                f"    [{batch_start + i + 1}/{len(video_links)}]",
            )
            for i, (video_idx, link) in enumerate(batch)
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                log(f"    Exception: {result}")
                continue
            video_idx, data = result
            if "error" not in data:
                channel = data["detail"].get("channel_name", "N/A")
                log(f"    OK: {channel} — {len(data.get('comments', []))} comments")
            results.append(data)

    return results


# ── Sequential fetch ────────────────────────────────────────────────
async def fetch_details_sequential(client, api_key, logger, videos, video_links, comment_count):
    log = logger.log
    comment_label = f", comments={comment_count}" if comment_count > 0 else ""
    log(f"\n[Step 3] Fetch details for {len(video_links)} videos (sequential{comment_label})")

    results = []

    for seq, (video_idx, link) in enumerate(video_links):
        if seq > 0:
            log(f"  (waiting {REQUEST_DELAY_SEC}s...)")
            await asyncio.sleep(REQUEST_DELAY_SEC)

        _, data = await _fetch_one(
            client, api_key, logger, videos, video_idx, link,
            comment_count,
            f"[{seq + 1}/{len(video_links)}]",
        )

        if "error" not in data:
            channel = data["detail"].get("channel_name", "")
            title = data["detail"].get("title", "")[:50]
            log(f"    detail: {channel} — {title} | views={data['detail'].get('view_count')}")
            log(f"    comments: {len(data['comments'])}")
        else:
            log(f"    Failed: {data['error']}")
        results.append(data)

    return results


def extract_video_links(videos):
    """Extract YouTube video links from search results."""
    links = []
    for i, video in enumerate(videos):
        link = video.get("link") or video.get("url")
        if link:
            if link.startswith("/"):
                link = f"https://www.youtube.com{link}"
            elif not link.startswith("http"):
                link = f"https://www.youtube.com/watch?v={link}"
            links.append((i, link))
    return links


# ── Main ────────────────────────────────────────────────────────────
async def collect_by_keyword(
    keyword: str,
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
    output_dir = Path(f"output/youtube_keyword_api_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "collect.log")
    log = logger.log

    t_start = time.time()

    async with httpx.AsyncClient() as client:
        try:
            log("=" * 60)
            log(f"YouTube Collect by Keyword (API) — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            log(f"  keyword:    {keyword}")
            log(f"  sort:       {sort}")
            log(f"  count:      {count} videos")
            log(f"  comments:   {comment_count} per video")
            log(f"  parallel:   {parallel}")
            log(f"  output:     {output_dir}")
            log("=" * 60)

            # ── Step 1: Search ────────────────────────────────────────
            videos = await search_videos(client, api_key, logger, keyword, sort, count)
            if not videos:
                return

            save_jsonl(videos, output_dir / "search_results.jsonl")
            log(f"  Saved {len(videos)} videos → {output_dir / 'search_results.jsonl'}")

            # ── Step 2: Fetch video details + comments ────────────────
            video_links = extract_video_links(videos)

            if video_links and parallel:
                results = await fetch_details_parallel(client, api_key, logger, videos, video_links, comment_count)
            elif video_links:
                results = await fetch_details_sequential(client, api_key, logger, videos, video_links, comment_count)
            else:
                results = []

            if results:
                output_file = output_dir / ("videos_with_comments.jsonl" if comment_count > 0 else "videos_with_details.jsonl")
                save_jsonl(results, output_file)
                log(f"\n  Saved {len(results)} results → {output_file}")

            # ── Summary ───────────────────────────────────────────────
            elapsed = time.time() - t_start
            total_comments = sum(len(r.get("comments", [])) for r in results) if results else 0

            log("\n" + "=" * 60)
            log("Summary:")
            log(f"  Search results: {len(videos)} videos")
            if results:
                ok = sum(1 for r in results if "error" not in r)
                log(f"  Details fetched: {ok}/{len(video_links)}")
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
                channel = detail.get("channel_name", "N/A")
                title = detail.get("title", "N/A")
                print(f"\n[Video {i+1}] {channel}")
                print(f"  title:        {str(title)[:120]}{'...' if len(str(title)) > 120 else ''}")
                print(f"  views:        {detail.get('view_count', 'N/A')}")
                print(f"  likes:        {detail.get('like_count', 'N/A')}")
                print(f"  duration:     {detail.get('duration', 'N/A')}")
                print(f"  uploaded:     {detail.get('upload_date', 'N/A')}")
                print(f"  subscribers:  {detail.get('subscriber_count', 'N/A')}")
                print(f"  comments:     {len(comments)} collected")
                for j, c in enumerate(comments[:2]):
                    c_author = c.get("author", "N/A")
                    c_text = c.get("text", "N/A")
                    print(f"    comment {j+1}: {c_author} — {str(c_text)[:80]}{'...' if len(str(c_text)) > 80 else ''}")
                if len(comments) > 2:
                    print(f"    ... and {len(comments) - 2} more comments")

            if not sample_results and videos:
                print("\n(No detail results — showing search videos)")
                for i, v in enumerate(videos[:3]):
                    channel = v.get("channel_name", "N/A")
                    title = v.get("title", "N/A")
                    print(f"  [{i+1}] {channel} — {str(title)[:100]}{'...' if len(str(title)) > 100 else ''}")

            print("=" * 60)

        finally:
            log(f"\nLog saved → {output_dir / 'collect.log'}")
            logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube — Collect Videos by Keyword (API)")
    parser.add_argument("--keyword", default="kpop", help="Search keyword")
    parser.add_argument("--sort", default="Relevance",
                        choices=["Relevance", "Upload date", "View count", "Rating"],
                        help="Sort order (default: Relevance)")
    parser.add_argument("--count", type=int, default=10, help="Number of videos to search")
    parser.add_argument("--comments", type=int, default=20,
                        help="Number of comments per video (0 = skip comments)")
    parser.add_argument("--parallel", action="store_true",
                        help="Use concurrent requests (fast). Default is sequential.")
    args = parser.parse_args()

    asyncio.run(collect_by_keyword(
        args.keyword, args.sort, args.count, args.comments, args.parallel,
    ))
