"""Serve the exported research results to the API layer.

Read-only, and **standard library only**. Everything here comes from the JSON
bundle that ``solaris.webexport`` writes into ``app/data/`` during
``python -m solaris.cli report``.

Two consequences, both deliberate:

* The deployed web function carries no pandas, numpy or pyarrow -- roughly
  250 MB of wheels it would otherwise need purely to slice a table that never
  changes. Cold starts stay fast and the serverless bundle stays small.
* The app physically cannot produce a number the pipeline did not export, so
  the "no hand-written metrics" rule is enforced by structure rather than by
  discipline.

If an export is missing, every endpoint says which command regenerates it
rather than rendering a plausible-looking placeholder.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

HEADLINE_MODEL = "lgbm_weather"
BASELINE_MODEL = "lgbm_calendar_history"
NAIVE_MODEL = "naive_lag_24h"

TARGET = "demand_mw"
POWER_UNIT = "MW"
TIMEZONE = "Asia/Kolkata"

# Which command regenerates each export, so an error can tell the user what
# to run instead of just reporting a missing file.
_REBUILD_COMMAND = {
    "overview": "python -m solaris.cli report",
    "models": "python -m solaris.cli report",
    "series": "python -m solaris.cli report",
    "daily_profile": "python -m solaris.cli report",
    "evening_stress": "python -m solaris.cli report",
    "explainability": "python -m solaris.cli report",
    "methodology": "python -m solaris.cli report",
    "figures_index": "python -m solaris.cli report",
}
_DEFAULT_COMMAND = "python -m solaris.cli all"


class ArtefactMissing(RuntimeError):
    """Raised when an exported artefact has not been generated yet."""

    def __init__(self, path: Path, command: str) -> None:
        self.path = path
        self.command = command
        super().__init__(f"Missing {path.name}. Run `{command}` to generate it.")


@lru_cache(maxsize=32)
def _load(name: str) -> Any:
    """Load one exported JSON document, cached for the process lifetime."""
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        raise ArtefactMissing(path, _REBUILD_COMMAND.get(name, _DEFAULT_COMMAND))
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


def overview() -> dict:
    return _load("overview")


def models() -> dict:
    return _load("models")


def model_table() -> list[dict]:
    return _load("models")["models"]


def daily_profile() -> dict:
    return _load("daily_profile")


def evening_stress() -> dict:
    return _load("evening_stress")


def explainability() -> dict:
    return _load("explainability")


def methodology() -> dict:
    return _load("methodology")


def model_version() -> dict:
    return _load("overview").get("model_version", {})


# --------------------------------------------------------------------------
# Time series
# --------------------------------------------------------------------------


def available_range() -> dict:
    payload = _load("series")
    return {
        "first": payload["first"],
        "last": payload["last"],
        "hours": payload["hours"],
    }


def _parse_date(value: str, field: str) -> datetime:
    """Parse an ISO date or datetime, raising a clear error on bad input."""
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field}={value!r} is not an ISO date") from exc


def series(
    start: str | None = None,
    end: str | None = None,
    max_hours: int | None = None,
) -> dict:
    """Hourly actual/predicted series over the test period, optionally windowed.

    Filtering is done on the ISO timestamp strings. They are all in a single
    fixed offset (+05:30, since India has no daylight saving) and ISO-8601
    timestamps in one offset sort lexicographically in chronological order, so
    a string comparison is correct here and avoids parsing 7,000 timestamps on
    every request.
    """
    payload = _load("series")
    timestamps: list[str] = payload["timestamps"]

    low, high = 0, len(timestamps)

    if start:
        _parse_date(start, "start")
        # A bare date means "from the first hour of that day".
        low = _bisect_left(timestamps, start)
    if end:
        _parse_date(end, "end")
        # A bare date means "through the last hour of that day", so compare
        # against the end of the day rather than its midnight.
        boundary = end if len(end) > 10 else end + "T23:59:59"
        high = _bisect_right(timestamps, boundary)

    if high < low:
        high = low

    if max_hours is not None and (high - low) > max_hours:
        low = high - max_hours

    return {
        "timestamps": timestamps[low:high],
        "unit": payload["unit"],
        "timezone": payload["timezone"],
        "columns": {
            name: values[low:high] for name, values in payload["columns"].items()
        },
    }


def _bisect_left(items: list[str], target: str) -> int:
    low, high = 0, len(items)
    while low < high:
        mid = (low + high) // 2
        if items[mid][: len(target)] < target:
            low = mid + 1
        else:
            high = mid
    return low


def _bisect_right(items: list[str], target: str) -> int:
    low, high = 0, len(items)
    while low < high:
        mid = (low + high) // 2
        if items[mid][: len(target)] <= target:
            low = mid + 1
        else:
            high = mid
    return low


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


def available_figures() -> list[str]:
    try:
        return _load("figures_index")["figures"]
    except ArtefactMissing:
        return []


def figure(name: str) -> dict:
    """Load one exported Plotly figure spec by name."""
    safe = Path(name).name  # never let a path escape the figures directory
    path = DATA_DIR / "figures" / f"{safe}.json"
    if not path.exists():
        raise ArtefactMissing(path, "python -m solaris.cli report")
    return json.loads(path.read_text(encoding="utf-8"))


def clear_cache() -> None:
    """Drop cached documents. Used by tests and after a pipeline re-run."""
    _load.cache_clear()
