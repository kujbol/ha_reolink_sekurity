"""Tests for metadata creation, JSON storage, and event indexing."""

import pytest
from pathlib import Path

from custom_components.reolink_ha_sekurity.metadata import (
    create_event_metadata,
    add_segment_to_metadata,
    complete_event_metadata,
    fail_event_metadata,
    save_event_metadata,
    load_event_metadata,
    save_events_index,
    load_events_index,
    append_to_events_index,
    get_media_base_path,
    get_camera_dir,
    get_event_dir,
)


def test_path_helpers():
    base = get_media_base_path("camera_on_nas/reolink_ha_sekurity")
    assert base == Path("/media/camera_on_nas/reolink_ha_sekurity")

    cam = get_camera_dir("camera_on_nas/reolink_ha_sekurity", "front_door")
    assert cam == base / "front_door"

    evt = get_event_dir("camera_on_nas/reolink_ha_sekurity", "front_door", "evt123")
    assert evt == cam / "evt123"


def test_create_and_update_event_metadata():
    data = create_event_metadata(
        event_id="evt123",
        camera_name="front_door",
        camera_entity="camera.front_door",
        trigger_entity="binary_sensor.front_door_person",
        event_type="person",
        lookback=5,
    )
    assert data["event_id"] == "evt123"
    assert data["status"] == "in_progress"
    assert data["segments"] == []

    add_segment_to_metadata(data, "evt123_seg001.mp4", 1, 30)
    assert len(data["segments"]) == 1
    assert data["segments"][0]["file"] == "evt123_seg001.mp4"

    complete_event_metadata(data)
    assert data["status"] == "complete"
    assert data["ended_at"] is not None


def test_fail_event_metadata():
    data = create_event_metadata(
        event_id="evt123",
        camera_name="front_door",
        camera_entity="camera.front_door",
        trigger_entity="binary_sensor.front_door_person",
        event_type="person",
        lookback=5,
    )
    fail_event_metadata(data, "Storage error")
    assert data["status"] == "error"
    assert data["error"] == "Storage error"
