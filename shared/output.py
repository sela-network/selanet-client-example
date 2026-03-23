"""Shared output utilities for Selanet examples."""

import json
from pathlib import Path


def save_jsonl(data: list[dict], filepath: str | Path) -> Path:
    """Save a list of dicts to a JSONL file.

    Args:
        data: List of dictionaries to save.
        filepath: Output file path.

    Returns:
        The resolved Path of the written file.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return filepath
