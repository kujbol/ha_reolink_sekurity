"""Tests for alarm condition logic."""

from unittest.mock import MagicMock
from custom_components.reolink_ha_sekurity.alarm import (
    should_notify,
    should_activate_lights,
)
from custom_components.reolink_ha_sekurity.const import (
    FULL_ALARM_ENTITY,
    NIGHT_ALARM_ENTITY,
)


def test_should_notify_full_alarm_on(mock_hass):
    # Full alarm switch ON
    mock_hass.states.get = MagicMock(
        side_effect=lambda entity: MagicMock(state="on") if entity == FULL_ALARM_ENTITY else MagicMock(state="off")
    )
    assert should_notify(mock_hass, True, "22:00", "07:00") is True


def test_should_notify_all_alarms_off(mock_hass):
    # Both alarms OFF
    mock_hass.states.get = MagicMock(return_value=MagicMock(state="off"))
    assert should_notify(mock_hass, False, "22:00", "07:00") is False


def test_should_activate_lights(mock_hass):
    # Full alarm switch ON
    mock_hass.states.get = MagicMock(
        side_effect=lambda entity: MagicMock(state="on") if entity == FULL_ALARM_ENTITY else MagicMock(state="off")
    )
    assert should_activate_lights(mock_hass, True, "22:00", "07:00") is True
