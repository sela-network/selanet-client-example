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

### API 키는 어디서 받나요?

[selanet.ai](https://selanet.ai)에서 가입 후 발급받을 수 있습니다. 발급받은 키를 `.env` 파일의 `SELA_API_KEY`에 설정하세요.

### SDK 버전과 API 버전의 차이는?

| | SDK (`selanet-sdk`) | API (`httpx`) |
|---|---|---|
| 초기화 | `SelaClient.with_api_key()` | httpx 클라이언트 직접 생성 |
| 병렬 수집 | `browse_parallel_collect()` 내장 | `asyncio.gather()` 직접 구현 |
| 연결 관리 | `client.shutdown()` 자동 정리 | 수동 관리 |
| 의존성 | `selanet-sdk` 필요 | `httpx`만 필요 |

SDK 버전은 편의 기능이 내장되어 있어 간편하고, API 버전은 외부 의존성이 적어 가볍습니다.

### 플랫폼별로 지원하는 기능이 다른가요?

| Platform | 상세 조회 | 댓글 수집 | 필터 |
|----------|:---------:|:---------:|------|
| Xiaohongshu | ✅ | ✅ (최대 450) | 기간, 정렬 |
| X (Twitter) | ✅ | ✅ (최대 50) | 언어, 날짜 범위, 최소 좋아요/리트윗, 검색 탭 |
| YouTube | ✅ | ✅ (최대 50) | 정렬 |
| LinkedIn | ❌ | ❌ | 게시 기간, 정렬 |

LinkedIn은 검색 결과에 개별 포스트 URL이 포함되지 않아 상세 조회 및 댓글 수집이 불가합니다.

### 출력 형식은?

JSONL (JSON Lines) 형식으로 `output/` 디렉토리에 저장됩니다. 각 실행마다 타임스탬프 폴더가 생성됩니다.

```
output/
  xhs_keyword_20250401_120000/
    search_results.jsonl
    notes_with_comments.jsonl
    collect.log
```

### `--parallel` 옵션은 어떻게 동작하나요?

상세 조회와 댓글 수집을 배치 단위로 동시 요청합니다. 순차 모드 대비 수집 속도가 빨라지지만, 요청 제한(rate limit)에 걸릴 수 있으므로 배치 크기가 플랫폼별로 조절되어 있습니다 (XHS: 9, X: 3).

### 타임아웃이나 요청 실패 시 어떻게 되나요?

각 요청은 자동으로 1회 재시도하며, 요청 간 5초 딜레이가 적용됩니다. 타임아웃 기본값은 요청당 4분, 전체 작업 10분입니다. 이 값들은 각 스크립트 상단의 상수에서 조정할 수 있습니다.

## Disclaimer

Users are solely responsible for ensuring compliance with all applicable laws, regulations, and platform terms of service. Data collection must respect the target platform's terms of service and applicable data protection regulations (GDPR, PIPL, CCPA, etc.). The authors assume no liability for misuse.

## License

[MIT](LICENSE)
