"""Persistent stream management for Reolink HA Sekurity.

Keeps camera RTSP streams alive 24/7 so that:
- Recording starts instantly when a sensor triggers
- The lookback buffer is always populated for pre-roll
- No warm-up delay on first segment
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

# How often to verify streams are still running
STREAM_CHECK_INTERVAL = timedelta(minutes=5)

# How long to wait after starting a cold stream before recording
STREAM_WARMUP_SECONDS = 3


class StreamKeeper:
    """Maintains persistent camera RTSP streams for instant recording.

    Uses HA's camera entity API to create streams with keepalive=True,
    which tells HA not to tear down the stream when no one is viewing it.
    """

    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._managed_entities: set[str] = set()
        self._cancel_check: CALLBACK_TYPE | None = None
        self._stream_failures: dict[str, int] = {}  # entity_id → consecutive failures

    async def async_start(self, camera_entities: list[str]) -> None:
        """Start keeping streams alive for the given camera entities."""
        self._managed_entities = set(camera_entities)

        for entity_id in camera_entities:
            await self._activate_stream(entity_id)

        # Schedule periodic health check
        self._cancel_check = async_track_time_interval(
            self.hass,
            self._periodic_check,
            STREAM_CHECK_INTERVAL,
        )
        _LOGGER.warning(
            "[SEKURITY] StreamKeeper started — keeping %d camera streams alive",
            len(camera_entities),
        )

    async def _activate_stream(self, entity_id: str) -> bool:
        """Activate and set keepalive on a camera stream.

        Returns True if the stream was successfully started.
        """
        try:
            camera = self._get_camera_entity(entity_id)
            if camera is None:
                _LOGGER.warning(
                    "[SEKURITY] Camera entity %s not found — cannot start stream",
                    entity_id,
                )
                return False

            if not hasattr(camera, "async_create_stream"):
                _LOGGER.warning(
                    "[SEKURITY] Camera %s does not support streaming",
                    entity_id,
                )
                return False

            stream = await camera.async_create_stream()
            if stream is None:
                _LOGGER.warning(
                    "[SEKURITY] No stream source for %s (camera offline?)",
                    entity_id,
                )
                return False

            stream.keepalive = True
            stream.start()
            self._stream_failures[entity_id] = 0
            _LOGGER.info(
                "[SEKURITY] Stream keepalive active for %s", entity_id
            )
            return True

        except Exception:
            self._stream_failures[entity_id] = (
                self._stream_failures.get(entity_id, 0) + 1
            )
            _LOGGER.exception(
                "[SEKURITY] Error activating stream for %s (failure #%d)",
                entity_id,
                self._stream_failures[entity_id],
            )
            return False

    def _get_camera_entity(self, entity_id: str):
        """Get the Camera entity object from HA's entity component."""
        component = self.hass.data.get("camera")
        if component is not None and hasattr(component, "get_entity"):
            return component.get_entity(entity_id)
        return None

    def is_stream_alive(self, entity_id: str) -> bool:
        """Check if the stream for a camera appears to be running."""
        camera = self._get_camera_entity(entity_id)
        if camera is None:
            return False
        stream = getattr(camera, "stream", None)
        return stream is not None

    async def ensure_stream_ready(self, entity_id: str) -> bool:
        """Ensure a camera's stream is running and has buffered data.

        Call this before recording. Returns True if the stream appears ready.
        If the stream was cold, waits for it to buffer before returning.
        """
        # Fast path: stream already running
        if self.is_stream_alive(entity_id):
            return True

        # Stream not running — try to (re-)activate it
        _LOGGER.warning(
            "[SEKURITY] Stream not active for %s — warming up before recording",
            entity_id,
        )
        ok = await self._activate_stream(entity_id)
        if ok:
            # Give it time to fill the lookback buffer
            await asyncio.sleep(STREAM_WARMUP_SECONDS)
            return True

        # Last resort: request a snapshot to force camera connection
        try:
            from homeassistant.components.camera import async_get_image

            await async_get_image(self.hass, entity_id, timeout=10)
            await asyncio.sleep(STREAM_WARMUP_SECONDS)
            _LOGGER.info(
                "[SEKURITY] Stream warmed up via snapshot for %s", entity_id
            )
            return True
        except Exception:
            _LOGGER.error(
                "[SEKURITY] Could not warm up stream for %s — "
                "recording may fail or have no lookback",
                entity_id,
            )
            return False

    async def _periodic_check(self, now=None) -> None:
        """Periodically verify streams are still running and restart if needed."""
        for entity_id in self._managed_entities:
            if not self.is_stream_alive(entity_id):
                failures = self._stream_failures.get(entity_id, 0)
                if failures < 10:
                    _LOGGER.info(
                        "[SEKURITY] Stream dropped for %s — restarting",
                        entity_id,
                    )
                    await self._activate_stream(entity_id)
                elif failures == 10:
                    _LOGGER.error(
                        "[SEKURITY] Stream for %s has failed %d times — "
                        "will keep trying but suppressing logs",
                        entity_id,
                        failures,
                    )
                    await self._activate_stream(entity_id)
                else:
                    # Still try, just don't log every time
                    await self._activate_stream(entity_id)

    def stop(self) -> None:
        """Stop the periodic health check.

        Does NOT tear down streams — HA manages their lifecycle.
        """
        if self._cancel_check:
            self._cancel_check()
            self._cancel_check = None
        _LOGGER.info("[SEKURITY] StreamKeeper stopped")
