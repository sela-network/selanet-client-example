# Selanet Client Examples

Example collection for the [Selanet SDK](https://selanet.io).

## Quick Start

```bash
git clone https://github.com/selanet/selanet-client-example.git
cd selanet-client-example

# Install dependencies
uv sync

# Set API key
cp .env.example .env
# Add your SELA_API_KEY (get one at https://selanet.io)

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
```

## Platforms

| Platform | Examples |
|----------|----------|
| [Xiaohongshu (RED)](platforms/xiaohongshu/) | Keyword search, parallel detail + comment collection |
| [X (Twitter)](platforms/x/) | Keyword search, parallel detail + reply collection |

## Sample Output

See [`examples/sample_output/`](examples/sample_output/) for example JSONL output.

## Requirements

- Python 3.10+
- [Selanet API Key](https://selanet.io)

## Disclaimer

Users are solely responsible for ensuring compliance with all applicable laws, regulations, and platform terms of service. Data collection must respect the target platform's terms of service and applicable data protection regulations (GDPR, PIPL, CCPA, etc.). The authors assume no liability for misuse.

## License

[MIT](LICENSE)
