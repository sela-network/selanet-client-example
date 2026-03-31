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

## Disclaimer

Users are solely responsible for ensuring compliance with all applicable laws, regulations, and platform terms of service. Data collection must respect the target platform's terms of service and applicable data protection regulations (GDPR, PIPL, CCPA, etc.). The authors assume no liability for misuse.

## License

[MIT](LICENSE)
