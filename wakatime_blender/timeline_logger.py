import datetime
import os
from typing import Optional

from . import settings

TIMELINE_DIR = os.path.join(settings.RESOURCES_DIR, "timeline")


def _ensure_directory() -> None:
    try:
        os.makedirs(TIMELINE_DIR, exist_ok=True)
    except Exception:
        # Directory creation failures should not break tracking; ignore silently.
        pass


def _timeline_path() -> str:
    _ensure_directory()
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    return os.path.join(TIMELINE_DIR, f"{today}.log")


def log_event(message: str) -> None:
    """Append a timestamped message to the timeline log."""
    if not message:
        return
    timestamp = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    line = f"{timestamp} - {message.strip()}"
    try:
        path = _timeline_path()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        # Timeline logging must never raise inside Blender handlers.
        pass


def log_operator_event(operator: Optional[object]) -> None:
    """Log a Blender operator execution with label and identifier."""
    if operator is None:
        return
    label = getattr(operator, "bl_label", None) or getattr(getattr(operator, "bl_rna", None), "name", None)
    identifier = getattr(operator, "bl_idname", None) or getattr(getattr(operator, "bl_rna", None), "identifier", None)
    if label and identifier:
        log_event(f"operator {label} ({identifier})")
    elif label:
        log_event(f"operator {label}")
    elif identifier:
        log_event(f"operator {identifier}")
    else:
        log_event("operator <unknown>")


def latest_log_path() -> Optional[str]:
    """Return the most recent timeline log file if it exists."""
    path = _timeline_path()
    return path if os.path.exists(path) else None
