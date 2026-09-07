"""Storage, NAS path verification, and filesystem utilities for Reolink HA Sekurity."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .const import EVENTS_INDEX_FILE, EVENT_METADATA_FILE

_LOGGER = logging.getLogger(__name__)


class MediaPathUnavailable(Exception):
    """Raised when the media base path (NAS mount) is not available."""


def _ensure_dir(path: Path) -> None:
    """Create directory if it doesn't exist."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        raise MediaPathUnavailable(
            f"Failed to create directory '{path}': {exc}"
        ) from exc


def _write_json(path: Path, data: dict | list) -> None:
    """Write JSON data to a file atomically."""
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        tmp_path.rename(path)
    except (OSError, PermissionError) as exc:
        _LOGGER.exception("Failed to write JSON to %s", path)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise MediaPathUnavailable(
            f"Failed to write JSON to '{path}': {exc}"
        ) from exc


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


def is_nas_mounted(mount_point: Path) -> bool:
    """Check if mount_point is an active mount point in Linux/HA."""
    if not mount_point.exists() or not mount_point.is_dir():
        return False

    # If mount_point is /media itself, treat as valid local storage
    if mount_point.resolve() == Path("/media").resolve():
        return True

    # 1. Standard Python mount check (checks st_dev difference)
    if os.path.ismount(mount_point):
        return True

    # 2. Linux /proc/mounts check
    try:
        proc_mounts = Path("/proc/mounts")
        if proc_mounts.exists():
            target_str = str(mount_point.resolve())
            with open(proc_mounts, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == target_str:
                        return True
    except Exception:
        pass

    return False


def verify_media_path(media_path: str) -> None:
    """Verify the media base path (NAS mount) exists, is mounted, and is writable."""
    mount_name = media_path.split("/")[0]
    mount_point = Path("/media") / mount_name

    if not is_nas_mounted(mount_point):
        raise MediaPathUnavailable(
            f"Media mount '/media/{mount_name}' is not mounted yet by Home Assistant. "
            f"Is the NAS mounted? Check Settings → System → Storage."
        )

    base_path = get_media_base_path(media_path)
    _ensure_dir(base_path)


def get_camera_dir(media_path: str, camera_name: str) -> Path:
    """Get the directory for a camera's data."""
    return get_media_base_path(media_path) / camera_name


def get_event_dir(media_path: str, camera_name: str, event_id: str) -> Path:
    """Get the directory for a specific event."""
    return get_camera_dir(media_path, camera_name) / event_id


def ensure_camera_dirs(media_path: str, camera_name: str) -> None:
    """Create the camera directory structure on the NAS."""
    verify_media_path(media_path)
    camera_dir = get_camera_dir(media_path, camera_name)
    _ensure_dir(camera_dir)


def ensure_event_dir(media_path: str, camera_name: str, event_id: str) -> Path:
    """Create and return the event directory."""
    verify_media_path(media_path)
    event_dir = get_event_dir(media_path, camera_name, event_id)
    _ensure_dir(event_dir)
    return event_dir
