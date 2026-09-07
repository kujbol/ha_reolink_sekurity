"""Pytest fixtures for Reolink HA Sekurity tests."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure custom_components is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set up Home Assistant mock module imports
import tests.ha_mock  # noqa: F401

from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.fixture
def mock_hass():
    """Mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.states = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    hass.states.async_set = MagicMock()
    hass.services = MagicMock()
    hass.services.has_service = MagicMock(return_value=True)
    hass.services.async_call = AsyncMock()
    hass.async_create_task = MagicMock(side_effect=lambda coroutine, name=None: coroutine)
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    hass.loop = MagicMock()
    hass.config = MagicMock()
    hass.config.config_dir = "/config"
    return hass


@pytest.fixture
def mock_config_entry():
    """Mock Home Assistant ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        "media_path": "camera_on_nas/reolink_ha_sekurity",
        "notify_targets": ["notify.mobile_app_test_phone"],
        "error_notify_targets": ["notify.mobile_app_admin_phone"],
        "night_start": "22:00",
        "night_end": "07:00",
        "light_entities": ["light.driveway_light"],
        "light_timeout": 300,
        "dashboard_path": "/dashboard-security/security",
        "cameras": {
            "front_door": {
                "camera_entity": "camera.front_door",
                "camera_name": "front_door",
                "record_sensors": ["binary_sensor.front_door_person"],
                "alarm_sensors": ["binary_sensor.front_door_person"],
                "clip_duration": 30,
                "max_duration": 300,
                "lookback": 5,
                "post_roll": 15,
            }
        },
    }
    return entry


@pytest.fixture
def tmp_media_dir(tmp_path):
    """Temporary media directory simulating /media."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir
