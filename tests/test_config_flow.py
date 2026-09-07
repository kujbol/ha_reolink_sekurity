"""Unit tests for config_flow.py."""

from __future__ import annotations

import pytest

from custom_components.reolink_ha_sekurity.config_flow import _derive_camera_name


def test_derive_camera_name():
    """Test camera name derivation from entity IDs."""
    assert _derive_camera_name("camera.front_door_fluent") == "front_door"
    assert _derive_camera_name("camera.front_door_clear") == "front_door"
    assert _derive_camera_name("camera.driveway") == "driveway"
    assert _derive_camera_name("camera.backyard_balanced") == "backyard"
