import json
import os
from datetime import date
from typing import Tuple

from . import settings

_STATE_PATH = os.path.join(settings.RESOURCES_DIR, "timeline", "daily_state.json")


def _ensure_directory() -> None:
    directory = os.path.dirname(_STATE_PATH)
    try:
        os.makedirs(directory, exist_ok=True)
    except Exception:
        pass


def load_tracked_seconds(day: date) -> Tuple[int, bool]:
    """Return previously saved tracked seconds for the given day.

    Returns a tuple of (seconds, exists) where exists indicates whether a
    persisted state was found for the provided date.
    """
    _ensure_directory()
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return 0, False
    stored_date = data.get("date")
    if stored_date != day.isoformat():
        return 0, False
    try:
        seconds = int(data.get("seconds", 0))
    except Exception:
        seconds = 0
    return max(0, seconds), True


def save_tracked_seconds(day: date, seconds: int) -> None:
    _ensure_directory()
    payload = {"date": day.isoformat(), "seconds": max(0, int(seconds))}
    try:
        with open(_STATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except Exception:
        pass
