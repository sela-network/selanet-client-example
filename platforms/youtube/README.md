# YouTube

## Usage

```bash
# Basic (sequential, 10 videos)
uv run platforms/youtube/collect_by_keyword_api.py --keyword "kpop" --count 10

# Parallel mode
uv run platforms/youtube/collect_by_keyword_api.py --keyword "AI" --count 20 --parallel

# Sort by upload date
uv run platforms/youtube/collect_by_keyword_api.py --keyword "python tutorial" --count 10 --sort "Upload date"

# Skip comments
uv run platforms/youtube/collect_by_keyword_api.py --keyword "kpop" --count 10 --parallel --comments 0

# Custom comment count
uv run platforms/youtube/collect_by_keyword_api.py --keyword "kpop" --count 10 --comments 50
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--keyword` | `kpop` | Search keyword |
| `--count` | `10` | Number of videos |
| `--comments` | `20` | Comments per video (0 = skip) |
| `--parallel` | off | Parallel batched collection |
| `--sort` | `Relevance` | `Relevance`, `Upload date`, `View count`, `Rating` |

## Output

JSONL files saved to `output/youtube_keyword_api_<timestamp>/`:

- `search_results.jsonl` — search results (video, short)
- `videos_with_comments.jsonl` — video details (video_info) + comments
