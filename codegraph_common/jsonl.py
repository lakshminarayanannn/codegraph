"""JSON Lines reading and writing. Uses orjson when installed (several times faster), else the stdlib."""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

try:  # optional speed-up: pip install orjson
    import orjson

    def _loads(data: bytes) -> Any:
        return orjson.loads(data)

    def _dumps(value: Any) -> bytes:
        return orjson.dumps(value)

except ImportError:  # pragma: no cover - exercised only without orjson
    def _loads(data: bytes) -> Any:
        return json.loads(data)

    def _dumps(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def read_jsonl(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    with open(path, "rb") as handle:
        for line in handle:
            if line.strip():
                yield _loads(line)


def write_jsonl(path: str | os.PathLike[str], records: Iterable[dict[str, Any]]) -> int:
    """Writes atomically (temp file + rename) and returns the number of records."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    count = 0
    with open(temp, "wb") as handle:
        for record in records:
            handle.write(_dumps(record))
            handle.write(b"\n")
            count += 1
    os.replace(temp, target)
    return count


def write_json(path: str | os.PathLike[str], value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(temp, target)
