# X (Twitter)

## Usage

```bash
# Basic (sequential, 10 tweets)
uv run platforms/x/collect_by_keyword.py --keyword "kpop" --count 10

# Parallel mode
uv run platforms/x/collect_by_keyword.py --keyword "AI" --count 50 --parallel

# Latest tweets with replies
uv run platforms/x/collect_by_keyword.py --keyword "python" --count 20 --search-tab Latest --comments 30

# Skip replies
uv run platforms/x/collect_by_keyword.py --keyword "kpop" --count 10 --parallel --comments 0

# With filters
uv run platforms/x/collect_by_keyword.py --keyword "AI" --count 20 --lang en --min-likes 100 --since 2025-01-01
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--keyword` | `kpop` | Search keyword |
| `--count` | `10` | Number of tweets |
| `--comments` | `20` | Replies per tweet (0 = skip) |
| `--parallel` | off | Parallel batched collection |
| `--search-tab` | `Top` | `Top`, `Latest`, `People`, `Media` |
| `--lang` | none | Language code (`en`, `ko`, `ja`, ...) |
| `--since` | none | Start date (`YYYY-MM-DD`) |
| `--until` | none | End date (`YYYY-MM-DD`) |
| `--min-likes` | none | Minimum likes filter |
| `--min-retweets` | none | Minimum retweets filter |

## Output

JSONL files saved to `output/x_keyword_<timestamp>/`:

- `search_results.jsonl` — search results
- `tweets_with_replies.jsonl` — tweet details + replies
