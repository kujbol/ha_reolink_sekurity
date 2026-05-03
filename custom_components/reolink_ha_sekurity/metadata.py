"""JSON metadata management for Reolink HA Sekurity."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .const import EVENTS_INDEX_FILE, EVENT_METADATA_FILE, MAX_EVENTS_INDEX

_LOGGER = logging.getLogger(__name__)


def _ensure_dir(path: Path) -> None:
    """Create directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: dict | list) -> None:
    """Write JSON data to a file atomically."""
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        tmp_path.rename(path)
    except OSError:
        _LOGGER.exception("Failed to write JSON to %s", path)
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _read_json(path: Path) -> dict | list | None:
    """Read JSON data from a file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        _LOGGER.exception("Failed to read JSON from %s", path)
        return None


def get_media_base_path(media_path: str) -> Path:
    """Get the absolute base path for media storage."""
    return Path("/media") / media_path


def get_camera_dir(media_path: str, camera_name: str) -> Path:
    """Get the directory for a camera's data."""
    return get_media_base_path(media_path) / camera_name


def get_event_dir(media_path: str, camera_name: str, event_id: str) -> Path:
    """Get the directory for a specific event."""
    return get_camera_dir(media_path, camera_name) / event_id


def ensure_camera_dirs(media_path: str, camera_name: str) -> None:
    """Create the camera directory structure on the NAS."""
    camera_dir = get_camera_dir(media_path, camera_name)
    _ensure_dir(camera_dir)


def ensure_event_dir(media_path: str, camera_name: str, event_id: str) -> Path:
    """Create and return the event directory."""
    event_dir = get_event_dir(media_path, camera_name, event_id)
    _ensure_dir(event_dir)
    return event_dir


def generate_event_id(camera_name: str) -> str:
    """Generate a unique event ID from timestamp and camera name."""
    now = datetime.now(timezone.utc).astimezone()
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{camera_name}"


def create_event_metadata(
    event_id: str,
    camera_name: str,
    camera_entity: str,
    trigger_entity: str,
    event_type: str,
    lookback: int,
) -> dict[str, Any]:
    """Create initial event metadata dict."""
    now = datetime.now(timezone.utc).astimezone()
    return {
        "event_id": event_id,
        "camera": camera_name,
        "camera_entity": camera_entity,
        "trigger_entity": trigger_entity,
        "event_type": event_type,
        "started_at": now.isoformat(),
        "ended_at": None,
        "status": "in_progress",
        "segments": [],
        "snapshot": None,
        "alarm_active": False,
        "notification_sent": False,
        "lights_activated": False,
        "error": None,
    }


def save_event_metadata(
    media_path: str, camera_name: str, event_id: str, metadata: dict
) -> None:
    """Write event metadata JSON file."""
    event_dir = get_event_dir(media_path, camera_name, event_id)
    _ensure_dir(event_dir)
    _write_json(event_dir / EVENT_METADATA_FILE, metadata)


def load_event_metadata(
    media_path: str, camera_name: str, event_id: str
) -> dict | None:
    """Load event metadata from JSON file."""
    event_dir = get_event_dir(media_path, camera_name, event_id)
    return _read_json(event_dir / EVENT_METADATA_FILE)


def add_segment_to_metadata(
    metadata: dict, segment_filename: str, index: int, duration: int
) -> None:
    """Add a segment entry to event metadata (in-memory)."""
    metadata["segments"].append(
        {
            "file": segment_filename,
            "index": index,
            "duration": duration,
        }
    )


def complete_event_metadata(metadata: dict) -> None:
    """Mark event as complete (in-memory)."""
    now = datetime.now(timezone.utc).astimezone()
    metadata["ended_at"] = now.isoformat()
    metadata["status"] = "complete"


def fail_event_metadata(metadata: dict, error_msg: str) -> None:
    """Mark event as failed (in-memory)."""
    now = datetime.now(timezone.utc).astimezone()
    metadata["ended_at"] = now.isoformat()
    metadata["status"] = "error"
    metadata["error"] = error_msg


# --- Events index (per-camera rolling list) ---


def _event_summary(metadata: dict) -> dict:
    """Create a summary entry for the events index."""
    return {
        "event_id": metadata["event_id"],
        "camera": metadata["camera"],
        "event_type": metadata["event_type"],
        "started_at": metadata["started_at"],
        "ended_at": metadata.get("ended_at"),
        "status": metadata["status"],
        "snapshot": metadata.get("snapshot"),
        "segment_count": len(metadata.get("segments", [])),
    }


def load_events_index(media_path: str, camera_name: str) -> list[dict]:
    """Load the events index for a camera."""
    camera_dir = get_camera_dir(media_path, camera_name)
    data = _read_json(camera_dir / EVENTS_INDEX_FILE)
    if data is None:
        return []
    if isinstance(data, dict):
        return data.get("events", [])
    return data


def save_events_index(
    media_path: str, camera_name: str, events: list[dict]
) -> None:
    """Save the events index for a camera."""
    camera_dir = get_camera_dir(media_path, camera_name)
    _ensure_dir(camera_dir)
    _write_json(camera_dir / EVENTS_INDEX_FILE, {"events": events})


def append_to_events_index(
    media_path: str, camera_name: str, metadata: dict
) -> None:
    """Append (or update) an event in the camera's rolling index."""
    events = load_events_index(media_path, camera_name)
    summary = _event_summary(metadata)

    # Update existing entry if present, otherwise append
    for i, ev in enumerate(events):
        if ev["event_id"] == metadata["event_id"]:
            events[i] = summary
            save_events_index(media_path, camera_name, events)
            return

    events.insert(0, summary)

    # Trim to max size
    if len(events) > MAX_EVENTS_INDEX:
        events = events[:MAX_EVENTS_INDEX]

    save_events_index(media_path, camera_name, events)


def load_all_events(
    media_path: str, camera_names: list[str], limit: int = 25, offset: int = 0
) -> list[dict]:
    """Load events from all cameras, sorted by time, with pagination."""
    all_events: list[dict] = []
    for camera_name in camera_names:
        events = load_events_index(media_path, camera_name)
        all_events.extend(events)

    # Sort by started_at descending
    all_events.sort(key=lambda e: e.get("started_at", ""), reverse=True)

    return all_events[offset : offset + limit]
