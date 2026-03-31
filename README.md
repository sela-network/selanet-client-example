# Selanet Client Examples

Example collection for the [Selanet SDK](https://selanet.ai).

## Quick Start

```bash
git clone https://github.com/sela-network/selanet-client-example.git
cd selanet-client-example

# Install dependencies
uv sync

# Set API key
cp .env.example .env
# Add your SELA_API_KEY (get one at https://selanet.ai)

# Run an example
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 10

# Run with comments
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 10 --comments 100

# Run with comments and parallel
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 10 --comments 100 --parallel

# X (Twitter) — search tweets
uv run platforms/x/collect_by_keyword.py --keyword "ai" --count 10

# X with replies and parallel
uv run platforms/x/collect_by_keyword.py --keyword "ai" --count 10 --comments 100 --parallel

# YouTube — search videos
uv run platforms/youtube/collect_by_keyword_api.py --keyword "ai" --count 10

# LinkedIn — search posts
uv run platforms/linkedin/collect_by_keyword.py --keyword "AI" --count 10
uv run platforms/linkedin/collect_by_keyword.py --keyword "startup" --count 20 --date-posted past-week --sort date

# ── API-only (no SDK, uses httpx) ──────────────────────────

# XHS via REST API
uv run platforms/xiaohongshu/collect_by_keyword_api.py --keyword "kpop" --count 10 --parallel

# X via REST API
uv run platforms/x/collect_by_keyword_api.py --keyword "ai" --count 10 --comments 10 --parallel

# LinkedIn via REST API
uv run platforms/linkedin/collect_by_keyword_api.py --keyword "AI" --count 10
```

## Platforms

| Platform | Examples |
|----------|----------|
| [Xiaohongshu (RED)](platforms/xiaohongshu/) | Keyword search, parallel detail + comment collection |
| [X (Twitter)](platforms/x/) | Keyword search, parallel detail + reply collection |
| [YouTube](platforms/youtube/) | Keyword search (REST API) |
| [LinkedIn](platforms/linkedin/) | Keyword search (SDK + REST API), date/sort filters |

## Requirements

- Python 3.10+
- [Selanet API Key](https://selanet.ai)

## FAQ

### Where do I get an API key?

Sign up at [selanet.ai](https://selanet.ai) and generate a key. Set it as `SELA_API_KEY` in your `.env` file.

### What is the difference between SDK and API versions?

| | SDK (`selanet-sdk`) | API (`httpx`) |
|---|---|---|
| Initialization | `SelaClient.with_api_key()` | Create httpx client directly |
| Parallel collection | Built-in `browse_parallel_collect()` | Manual `asyncio.gather()` |
| Connection management | Auto cleanup via `client.shutdown()` | Manual management |
| Dependencies | Requires `selanet-sdk` | Only `httpx` |

The SDK version provides built-in convenience features. The API version is lighter with fewer external dependencies.

### Do supported features vary by platform?

| Platform | Detail fetch | Comments | Filters |
|----------|:-----------:|:--------:|---------|
| Xiaohongshu | ✅ | ✅ (up to 450) | Period, sort |
| X (Twitter) | ✅ | ✅ (up to 50) | Language, date range, min likes/retweets, search tab |
| YouTube | ✅ | ✅ (up to 50) | Sort |
| LinkedIn | ❌ | ❌ | Date posted, sort |

LinkedIn search results do not include individual post URLs, so detail and comment fetching is not available.

### What is the output format?

Results are saved in JSONL (JSON Lines) format under the `output/` directory. Each run creates a timestamped folder.

```
output/
  xhs_keyword_20250401_120000/
    search_results.jsonl
    notes_with_comments.jsonl
    collect.log
```

### How does the `--parallel` option work?

It sends detail and comment requests concurrently in batches. This is faster than sequential mode but may hit rate limits, so batch sizes are tuned per platform (XHS: 9, X: 3).

### What happens on timeout or request failure?

Each request is automatically retried once with a 5-second delay between requests. Default timeouts are 4 minutes per request and 10 minutes for the overall operation. These values can be adjusted via constants at the top of each script.

## Disclaimer

Users are solely responsible for ensuring compliance with all applicable laws, regulations, and platform terms of service. Data collection must respect the target platform's terms of service and applicable data protection regulations (GDPR, PIPL, CCPA, etc.). The authors assume no liability for misuse.

## License

[MIT](LICENSE)
