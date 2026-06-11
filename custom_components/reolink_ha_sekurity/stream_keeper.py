"""Persistent stream management for Reolink HA Sekurity.

Keeps camera RTSP streams alive 24/7 so that:
- Recording starts instantly when a sensor triggers
- The lookback buffer is always populated for pre-roll
- No warm-up delay on first segment

Recovers streams reactively when cameras go unavailable/available.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

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
        self._cancel_state_listener: CALLBACK_TYPE | None = None
        self._stream_failures: dict[str, int] = {}  # entity_id → consecutive failures
        self._recently_unavailable: set[str] = set()  # cameras that recently went unavailable

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

        # React to camera availability changes
        self._cancel_state_listener = async_track_state_change_event(
            self.hass,
            list(camera_entities),
            self._on_camera_state_change,
        )

        _LOGGER.warning(
            "[SEKURITY] StreamKeeper started — keeping %d camera streams alive "
            "(reactive recovery enabled)",
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
            _LOGGER.warning(
                "[SEKURITY] Stream ACTIVATED for %s "
                "(keepalive=True, stream=%s, prev_failures=%d)",
                entity_id, type(stream).__name__,
                self._stream_failures.get(entity_id, 0),
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
        """Check if the stream for a camera is genuinely running.

        Checks both the stream object existence AND the camera's HA state.
        A stale stream object from a camera that went unavailable is NOT alive.
        """
        # First check: is the camera entity available in HA?
        state = self.hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            _LOGGER.debug(
                "[SEKURITY] is_stream_alive(%s) = False "
                "(camera state=%s)",
                entity_id, state.state if state else "not_found",
            )
            return False

        # Second check: was this camera recently unavailable?
        # (stream object may be stale even though camera is back)
        if entity_id in self._recently_unavailable:
            _LOGGER.debug(
                "[SEKURITY] is_stream_alive(%s) = False "
                "(recently_unavailable, state=%s)",
                entity_id, state.state,
            )
            return False

        # Third check: does the stream object exist?
        camera = self._get_camera_entity(entity_id)
        if camera is None:
            _LOGGER.debug(
                "[SEKURITY] is_stream_alive(%s) = False "
                "(camera entity object not found)",
                entity_id,
            )
            return False
        stream = getattr(camera, "stream", None)
        alive = stream is not None
        if not alive:
            _LOGGER.debug(
                "[SEKURITY] is_stream_alive(%s) = False "
                "(no stream object on camera entity)",
                entity_id,
            )
        return alive

    async def ensure_stream_ready(self, entity_id: str) -> bool:
        """Ensure a camera's stream is running and has buffered data.

        Call this before recording. Returns True if the stream appears ready.
        If the stream was cold, waits for it to buffer before returning.
        """
        # Check if camera is available at all
        state = self.hass.states.get(entity_id)
        cam_state = state.state if state else "not_found"
        _LOGGER.info(
            "[SEKURITY] ensure_stream_ready(%s): cam_state=%s, "
            "recently_unavailable=%s, stream_failures=%d",
            entity_id, cam_state,
            entity_id in self._recently_unavailable,
            self._stream_failures.get(entity_id, 0),
        )

        if state is not None and state.state == STATE_UNAVAILABLE:
            _LOGGER.warning(
                "[SEKURITY] ensure_stream_ready(%s) -> FAIL: "
                "camera is UNAVAILABLE",
                entity_id,
            )
            return False

        # If recently unavailable, force a fresh stream activation
        if entity_id in self._recently_unavailable:
            _LOGGER.warning(
                "[SEKURITY] ensure_stream_ready(%s): camera was recently "
                "unavailable — forcing fresh stream activation",
                entity_id,
            )
            self._recently_unavailable.discard(entity_id)
            ok = await self._activate_stream(entity_id)
            _LOGGER.info(
                "[SEKURITY] ensure_stream_ready(%s): fresh activation "
                "result=%s",
                entity_id, ok,
            )
            if ok:
                await asyncio.sleep(STREAM_WARMUP_SECONDS)
                return True
            # Fall through to snapshot fallback

        # Fast path: stream already running
        if self.is_stream_alive(entity_id):
            _LOGGER.debug(
                "[SEKURITY] ensure_stream_ready(%s) -> OK (fast path)",
                entity_id,
            )
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
                state = self.hass.states.get(entity_id)
                cam_state = state.state if state else "unknown"

                if cam_state == STATE_UNAVAILABLE:
                    _LOGGER.debug(
                        "[SEKURITY] Stream check: %s is unavailable — "
                        "skipping restart until camera comes back",
                        entity_id,
                    )
                    continue

                if failures < 10:
                    _LOGGER.info(
                        "[SEKURITY] Stream dropped for %s (state=%s) — restarting",
                        entity_id, cam_state,
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

    @callback
    def _on_camera_state_change(self, event: Event) -> None:
        """React to camera availability changes."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        entity_id = new_state.entity_id

        if new_state.state == STATE_UNAVAILABLE:
            # Camera went down — mark it so we force a fresh stream later
            self._recently_unavailable.add(entity_id)
            _LOGGER.warning(
                "[SEKURITY] Camera %s went UNAVAILABLE — stream is now stale",
                entity_id,
            )
        elif (
            old_state is not None
            and old_state.state == STATE_UNAVAILABLE
            and new_state.state != STATE_UNAVAILABLE
        ):
            # Camera came back — immediately restart the stream
            _LOGGER.warning(
                "[SEKURITY] Camera %s is back (state=%s) — "
                "restarting stream immediately",
                entity_id, new_state.state,
            )
            self.hass.async_create_task(
                self._activate_stream(entity_id),
                f"sekurity_stream_restart_{entity_id}",
            )
            # Clear recently_unavailable after activation is scheduled
            self._recently_unavailable.discard(entity_id)

    def stop(self) -> None:
        """Stop the periodic health check and state listener.

        Does NOT tear down streams — HA manages their lifecycle.
        """
        if self._cancel_check:
            self._cancel_check()
            self._cancel_check = None
        if self._cancel_state_listener:
            self._cancel_state_listener()
            self._cancel_state_listener = None
        _LOGGER.info("[SEKURITY] StreamKeeper stopped")
