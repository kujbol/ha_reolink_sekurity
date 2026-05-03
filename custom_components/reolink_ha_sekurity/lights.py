"""Outside light control for Reolink HA Sekurity."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import DEFAULT_LIGHT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class LightController:
    """Manages outside lights activation and auto-off."""

    def __init__(self, hass: HomeAssistant, light_entities: list[str], timeout: int):
        self._hass = hass
        self._light_entities = light_entities
        self._timeout = timeout
        self._cancel_timer: callback | None = None

    async def activate(self) -> None:
        """Turn on all configured outside lights.

        If lights are already on (timer running), does NOT reset the timer.
        Simple approach: ignore re-triggers.
        """
        if not self._light_entities:
            return

        if self._cancel_timer is not None:
            # Lights already on with timer running — ignore re-trigger
            _LOGGER.debug("Lights already on, ignoring re-trigger")
            return

        _LOGGER.info("Activating outside lights: %s", self._light_entities)
        for entity_id in self._light_entities:
            try:
                await self._hass.services.async_call(
                    "light",
                    "turn_on",
                    {"entity_id": entity_id},
                    blocking=False,
                )
            except Exception:
                _LOGGER.exception("Failed to turn on light %s", entity_id)

        # Schedule auto-off
        self._cancel_timer = async_call_later(
            self._hass, self._timeout, self._auto_off
        )

    async def _auto_off(self, _now) -> None:
        """Turn off all outside lights after timeout."""
        _LOGGER.info("Auto-off: turning off outside lights")
        self._cancel_timer = None
        for entity_id in self._light_entities:
            try:
                await self._hass.services.async_call(
                    "light",
                    "turn_off",
                    {"entity_id": entity_id},
                    blocking=False,
                )
            except Exception:
                _LOGGER.exception("Failed to turn off light %s", entity_id)

    def cancel(self) -> None:
        """Cancel any pending auto-off timer (used during teardown)."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None
