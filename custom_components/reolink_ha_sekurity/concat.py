"""FFmpeg stream concatenation engine for Reolink HA Sekurity."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def merge_event_segments(event_dir: Path, metadata: dict) -> str | None:
    """Concatenate segment files into a single event.mp4 file using ffmpeg -c copy."""
    raw_segments = metadata.get("segments", [])
    if not raw_segments:
        return None

    # Only include valid non-empty files (>1KB)
    segments = []
    for s in raw_segments:
        seg_path = event_dir / s["file"]
        if seg_path.exists() and seg_path.stat().st_size > 1024:
            segments.append(s)

    if not segments:
        return None

    event_mp4 = event_dir / "event.mp4"
    if event_mp4.exists() and event_mp4.stat().st_size > 1024:
        return "event.mp4"

    if len(segments) == 1:
        seg_file = event_dir / segments[0]["file"]
        try:
            import shutil
            shutil.copyfile(seg_file, event_mp4)
            return "event.mp4"
        except Exception:
            pass

    filelist_path = event_dir / "concat_list.txt"
    try:
        lines = [f"file '{s['file']}'" for s in segments]
        filelist_path.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(filelist_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(event_mp4),
        ]
        res = subprocess.run(
            cmd,
            cwd=str(event_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if filelist_path.exists():
            try:
                filelist_path.unlink()
            except OSError:
                pass

        if res.returncode == 0 and event_mp4.exists() and event_mp4.stat().st_size > 1024:
            _LOGGER.info(
                "Merged %d segments into single event.mp4 for %s",
                len(segments), event_dir.name,
            )
            return "event.mp4"
    except Exception as exc:
        _LOGGER.warning("Failed to merge segments to event.mp4 for %s: %s", event_dir.name, exc)
        if filelist_path.exists():
            try:
                filelist_path.unlink()
            except OSError:
                pass

    return None
