"""Tests for stream concatenation and single MP4 merging."""

from unittest.mock import patch, MagicMock
from pathlib import Path
from custom_components.reolink_ha_sekurity.metadata import merge_event_segments


def test_merge_event_segments_single(tmp_path):
    event_dir = tmp_path / "event1"
    event_dir.mkdir()
    seg_file = event_dir / "seg001.mp4"
    seg_file.write_bytes(b"A" * 2000)

    metadata = {
        "segments": [{"file": "seg001.mp4", "duration": 30}]
    }

    result = merge_event_segments(event_dir, metadata)
    assert result == "event.mp4"
    assert (event_dir / "event.mp4").exists()


def test_merge_event_segments_empty(tmp_path):
    event_dir = tmp_path / "event2"
    event_dir.mkdir()
    metadata = {"segments": []}

    result = merge_event_segments(event_dir, metadata)
    assert result is None


def test_merge_event_segments_concat(tmp_path):
    event_dir = tmp_path / "event3"
    event_dir.mkdir()

    seg1 = event_dir / "seg001.mp4"
    seg2 = event_dir / "seg002.mp4"
    seg1.write_bytes(b"A" * 2000)
    seg2.write_bytes(b"B" * 2000)

    metadata = {
        "segments": [
            {"file": "seg001.mp4", "duration": 30},
            {"file": "seg002.mp4", "duration": 30},
        ]
    }

    def mock_ffmpeg(cmd, **kw):
        (event_dir / "event.mp4").write_bytes(b"C" * 4000)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=mock_ffmpeg) as mock_run:
        result = merge_event_segments(event_dir, metadata)

        assert result == "event.mp4"
        assert mock_run.called
