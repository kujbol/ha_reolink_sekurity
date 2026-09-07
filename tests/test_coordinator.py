"""Unit tests for ReolinkHaSekurityCoordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.reolink_ha_sekurity.coordinator import ReolinkHaSekurityCoordinator
from custom_components.reolink_ha_sekurity.storage import MediaPathUnavailable


def test_coordinator_properties(mock_hass, mock_config_entry):
    """Test coordinator property accessors."""
    coord = ReolinkHaSekurityCoordinator(mock_hass, mock_config_entry)
    assert coord.media_path == "camera_on_nas/reolink_ha_sekurity"
    assert coord.notify_targets == ["notify.mobile_app_test_phone"]
    assert coord.error_notify_targets == ["notify.mobile_app_admin_phone"]
    assert coord.night_start == "22:00"
    assert coord.night_end == "07:00"
    assert coord.dashboard_path == "/dashboard-security/security"
    assert "front_door" in coord.cameras


@pytest.mark.asyncio
async def test_coordinator_setup_and_teardown(mock_hass, mock_config_entry, tmp_media_dir):
    """Test async_setup and async_teardown lifecycle."""
    mock_config_entry.data["media_path"] = str(tmp_media_dir)
    coord = ReolinkHaSekurityCoordinator(mock_hass, mock_config_entry)

    with patch("custom_components.reolink_ha_sekurity.coordinator.async_track_state_change_event") as mock_track, \
         patch("custom_components.reolink_ha_sekurity.coordinator.async_track_time_interval") as mock_interval:
        await coord.async_setup()
        assert mock_track.called
        assert mock_interval.called

    await coord.async_teardown()
    assert len(coord.active_events) == 0


@pytest.mark.asyncio
async def test_coordinator_nas_unavailable(mock_hass, mock_config_entry):
    """Test handling when NAS media path is unavailable."""
    coord = ReolinkHaSekurityCoordinator(mock_hass, mock_config_entry)
    coord.cameras["front_door"]["sensor_debounce"] = 0
    coord._sensor_to_camera["binary_sensor.front_door_person"] = "front_door"
    
    event_mock = MagicMock()
    event_mock.data = {
        "entity_id": "binary_sensor.front_door_person",
        "new_state": MagicMock(state="on"),
        "old_state": MagicMock(state="off"),
    }

    with patch("custom_components.reolink_ha_sekurity.coordinator.verify_media_path", side_effect=MediaPathUnavailable("Disk unmounted")), \
         patch("custom_components.reolink_ha_sekurity.coordinator.send_error_notification", new_callable=AsyncMock) as mock_send_err:
        await coord._on_sensor_change(event_mock)
        assert mock_send_err.called
        assert "front_door" not in coord.active_events
