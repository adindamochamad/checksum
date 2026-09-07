"""Disk-backed cache of completed verifications.

The free Gemini tier allows twenty requests per day, per model, per project. A single
verification spends three. Judging runs for two weeks on a public link, so a live
model call per visitor is not a rate-limit risk -- it is a guarantee that most judges
see an error.

So preset questions are answered from a verification that already ran, replayed from
disk. The SQL, the row counts and the rankings in a replay are the ones that really
executed; nothing is invented, and the cache survives a restart so warming it costs
quota once rather than once per deploy.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

CACHE_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "warm_cache.json"


def load() -> dict[str, list[dict]]:
    try:
        raw = json.loads(CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, list)}


def save(cache: dict[str, list[dict]]) -> None:
    """Write atomically. A half-written cache would be worse than no cache."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", dir=CACHE_PATH.parent, delete=False, encoding="utf-8"
    )
    try:
        json.dump(cache, handle, ensure_ascii=False)
        handle.flush()
        handle.close()
        pathlib.Path(handle.name).replace(CACHE_PATH)
    except Exception:
        pathlib.Path(handle.name).unlink(missing_ok=True)
        raise
