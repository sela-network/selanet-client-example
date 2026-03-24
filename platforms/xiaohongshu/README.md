# Xiaohongshu (RED)

## Usage

```bash
# Basic (sequential, 10 notes)
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 10

# Parallel mode
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 50 --parallel

# Custom comment count
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 20 --comments 30

# Skip comments
uv run platforms/xiaohongshu/collect_by_keyword.py --keyword "kpop" --count 10 --parallel --comments 0
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--keyword` | `kpop` | Search keyword |
| `--count` | `10` | Number of notes |
| `--comments` | `20` | Comments per note (0 = skip) |
| `--parallel` | off | Parallel batched collection |
| `--period` | `all` | `day`, `week`, `half_year`, `all` |
| `--sort` | `most_liked` | `comprehensive`, `newest`, `most_liked`, `most_commented`, `most_collected` |

## Output

JSONL files saved to `output/xhs_keyword_<timestamp>/`:

- `search_results.jsonl` — search results
- `notes_with_comments.jsonl` — note details + comments
