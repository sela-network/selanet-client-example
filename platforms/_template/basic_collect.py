"""
[Platform Name] — Basic Collection Example

Replace this docstring with your platform-specific description.

Usage:
    python platforms/<platform>/basic_collect.py --query "keyword"
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from selanet_sdk import SelaClient, BrowseOptions

from shared import create_logger, timed, save_jsonl

load_dotenv()

DEFAULT_TIMEOUT_MS = 240_000


async def collect(query: str, count: int):
    api_key = os.getenv("SELA_API_KEY")
    if not api_key:
        print("ERROR: SELA_API_KEY not set in .env")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"output/<platform>_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = create_logger(output_dir / "run.log")
    log = logger.log

    log(f"Collect — query={query}, count={count}")
    log(f"Output: {output_dir}\n")

    # Initialize client
    client, err = await timed(logger, "SelaClient.with_api_key", SelaClient.with_api_key(api_key), timeout=30)
    if not client:
        log(f"FATAL: Cannot create client: {err}")
        logger.close()
        return

    # TODO: Implement platform-specific browse call
    # response, err = await timed(
    #     logger,
    #     "browse",
    #     client.browse(
    #         url="https://...",
    #         options=BrowseOptions(
    #             count=count,
    #             parse_only=True,
    #             timeout_ms=DEFAULT_TIMEOUT_MS,
    #         ),
    #     ),
    #     timeout=360,
    # )

    await timed(logger, "client.shutdown()", client.shutdown(), timeout=15)
    logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="[Platform] Basic Collection")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--count", type=int, default=10, help="Number of items")
    args = parser.parse_args()

    asyncio.run(collect(args.query, args.count))
