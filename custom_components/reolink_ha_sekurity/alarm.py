"""Dual alarm evaluation for Reolink HA Sekurity."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from homeassistant.core import HomeAssistant

from .const import FULL_ALARM_ENTITY, NIGHT_ALARM_ENTITY

_LOGGER = logging.getLogger(__name__)


def _parse_time(time_str: str) -> time:
    """Parse a HH:MM string into a time object."""
    parts = time_str.split(":")
    return time(int(parts[0]), int(parts[1]))


def is_night_hours(night_start: str, night_end: str) -> bool:
    """Check if the current local time falls within night hours.

    Handles spans crossing midnight (e.g., 22:00 - 07:00).
    """
    now = datetime.now().time()
    start = _parse_time(night_start)
    end = _parse_time(night_end)

    if start <= end:
        # Simple case: e.g., 06:00 - 18:00
        return start <= now <= end
    else:
        # Crosses midnight: e.g., 22:00 - 07:00
        return now >= start or now <= end


def is_alarm_active(hass: HomeAssistant, night_start: str, night_end: str) -> bool:
    """Check if any alarm is currently active.

    Returns True if:
    - Full alarm is ON (24/7), OR
    - Night alarm is ON AND current time is within night hours
    """
    full_state = hass.states.get(FULL_ALARM_ENTITY)
    night_state = hass.states.get(NIGHT_ALARM_ENTITY)

    full_alarm_on = full_state is not None and full_state.state == "on"
    night_alarm_on = night_state is not None and night_state.state == "on"

    if full_alarm_on:
        return True

    if night_alarm_on and is_night_hours(night_start, night_end):
        return True

    return False


def should_notify(
    hass: HomeAssistant,
    alarm_participation: bool,
    night_start: str,
    night_end: str,
) -> bool:
    """Determine if a notification should be sent for this event.

    Takes into account per-camera alarm participation flag.
    """
    if not alarm_participation:
        return False
    return is_alarm_active(hass, night_start, night_end)


def should_activate_lights(
    hass: HomeAssistant,
    alarm_participation: bool,
    night_start: str,
    night_end: str,
) -> bool:
    """Determine if outside lights should be activated.

    Same logic as notifications — lights activate when alarm is active
    and the camera participates in alarm.
    """
    if not alarm_participation:
        return False
    return is_alarm_active(hass, night_start, night_end)
