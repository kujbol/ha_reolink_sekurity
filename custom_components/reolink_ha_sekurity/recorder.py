"""Continuous segmented recording engine for Reolink HA Sekurity."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_CLIP_DURATION,
    DEFAULT_LOOKBACK,
    DEFAULT_MERGE_WINDOW,
    DEFAULT_POST_ROLL,
    DEFAULT_SEGMENT_OVERLAP,
    EVENT_TYPE_PRIORITY,
)
from .metadata import (
    add_segment_to_metadata,
    append_to_events_index,
    complete_event_metadata,
    create_event_metadata,
    ensure_event_dir,
    fail_event_metadata,
    generate_event_id,
    save_event_metadata,
)

_LOGGER = logging.getLogger(__name__)


class EventRecorder:
    """Records continuously while sensor is active, splitting into segments."""

    def __init__(
        self,
        hass: HomeAssistant,
        camera_entity: str,
        camera_name: str,
        trigger_entity: str,
        event_type: str,
        media_path: str,
        clip_duration: int = DEFAULT_CLIP_DURATION,
        max_duration: int = 300,
        lookback: int = DEFAULT_LOOKBACK,
        post_roll: int = DEFAULT_POST_ROLL,
        merge_window: int = DEFAULT_MERGE_WINDOW,
    ):
        self.hass = hass
        self.camera_entity = camera_entity
        self.camera_name = camera_name
        self.media_path = media_path
        self.clip_duration = clip_duration
        self.max_duration = max_duration
        self.lookback = lookback
        self.post_roll = post_roll
        self.merge_window = merge_window

        # Event state
        self.event_id = generate_event_id(camera_name)
        self.event_data = create_event_metadata(
            event_id=self.event_id,
            camera_name=camera_name,
            camera_entity=camera_entity,
            trigger_entity=trigger_entity,
            event_type=event_type,
        lookback=lookback,
        )
        self.event_dir: Path | None = None
        self.task: asyncio.Task | None = None

        # Control flags
        self._sensor_on = True
        self._sensor_off_time: datetime | None = None
        self._segment_index = 0
        self._stopped = False

    @property
    def is_running(self) -> bool:
        """Check if the recorder is still running."""
        return self.task is not None and not self.task.done()

    def sensor_off(self) -> None:
        """Signal that the trigger sensor has turned off."""
        if self._sensor_on:
            self._sensor_on = False
            self._sensor_off_time = datetime.now(timezone.utc)
            _LOGGER.debug(
                "Sensor off for event %s — will stop after post-roll", self.event_id
            )

    def sensor_on_again(self) -> None:
        """Signal that the trigger sensor has turned back on (within merge window)."""
        self._sensor_on = True
        self._sensor_off_time = None
        _LOGGER.debug("Sensor re-activated for event %s", self.event_id)

    def upgrade_event_type(self, trigger_entity: str) -> None:
        """Upgrade event type if a higher-priority detection fires."""
        new_type = self._detect_event_type(self.hass, trigger_entity)
        current_priority = EVENT_TYPE_PRIORITY.get(
            self.event_data["event_type"], 0
        )
        new_priority = EVENT_TYPE_PRIORITY.get(new_type, 0)

        if new_priority > current_priority:
            _LOGGER.info(
                "Upgrading event %s from %s to %s",
                self.event_id,
                self.event_data["event_type"],
                new_type,
            )
            self.event_data["event_type"] = new_type
            self.event_data["trigger_entity"] = trigger_entity

    @staticmethod
    def _detect_event_type(hass: HomeAssistant, entity_id: str) -> str:
        """Derive event type from the binary sensor entity ID, unique ID, or original name."""
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get(entity_id)

        check_strings = [entity_id.lower()]
        if entry:
            if entry.unique_id:
                check_strings.append(entry.unique_id.lower())
            if entry.original_name:
                check_strings.append(entry.original_name.lower())

        for check_str in check_strings:
            for event_type in ("person", "visitor", "vehicle", "pet", "animal"):
                # Check for common patterns like _person, or just the word "person"
                if f"_{event_type}" in check_str or f" {event_type}" in check_str or f"-{event_type}" in check_str:
                    return event_type
        return "motion"

    def _should_continue(self) -> bool:
        """Determine if we should record another segment."""
        if self._stopped:
            return False

        # Enforce max duration to prevent infinite recordings
        event_started_at = datetime.fromisoformat(self.event_data["started_at"])
        elapsed_total = (datetime.now(timezone.utc) - event_started_at).total_seconds()
        
        if elapsed_total >= self.max_duration:
            _LOGGER.warning(
                "Event %s reached max_duration (%ds). Forcing stop.",
                self.event_id,
                self.max_duration,
            )
            return False

        if self._sensor_on:
            return True

        # Sensor is off — check if we're still in post-roll or merge window
        if self._sensor_off_time is None:
            return False

        elapsed = (
            datetime.now(timezone.utc) - self._sensor_off_time
        ).total_seconds()

        # Keep recording during post-roll period
        if elapsed < self.post_roll:
            return True

        # After post-roll, wait for merge window
        if elapsed < self.post_roll + self.merge_window:
            return True

        return False

    async def take_snapshot(self) -> str | None:
        """Take a camera snapshot for the event thumbnail."""
        if self.event_dir is None:
            return None

        snapshot_filename = "snapshot.jpg"
        snapshot_path = self.event_dir / snapshot_filename

        try:
            from homeassistant.components.camera import async_get_image
            
            image = await async_get_image(self.hass, self.camera_entity)
            if image and image.content:
                def write_image():
                    with open(snapshot_path, "wb") as f:
                        f.write(image.content)
                        
                await self.hass.async_add_executor_job(write_image)
                
                self.event_data["snapshot"] = snapshot_filename
                _LOGGER.debug("Snapshot saved: %s", snapshot_path)
                return snapshot_filename
            else:
                _LOGGER.warning("Failed to get image content from %s", self.camera_entity)
                return None
        except Exception:
            _LOGGER.exception("Failed to take snapshot for %s", self.event_id)
            return None

    async def _record_segment(self) -> str | None:
        """Record a single segment. Returns the segment filename or None on failure."""
        self._segment_index += 1
        segment_filename = f"{self.event_id}_seg{self._segment_index:03d}.mp4"
        segment_path = self.event_dir / segment_filename

        # First segment uses full lookback; subsequent segments use overlap
        current_lookback = (
            self.lookback if self._segment_index == 1 else DEFAULT_SEGMENT_OVERLAP
        )

        # Track timing to detect when camera.record returns too fast (stream failure)
        segment_start = datetime.now(timezone.utc)

        try:
            _LOGGER.debug(
                "Recording segment %d for %s (duration=%d, lookback=%d)",
                self._segment_index,
                self.event_id,
                self.clip_duration,
                current_lookback,
            )
            await self.hass.services.async_call(
                "camera",
                "record",
                {
                    "entity_id": self.camera_entity,
                    "filename": str(segment_path),
                    "duration": self.clip_duration,
                    "lookback": current_lookback,
                },
                blocking=True,
            )

            # Verify the file was actually created with content.
            # camera.record can return without error but fail to capture
            # anything (HA stream logs "Recording failed to capture anything"
            # internally without raising).
            file_ok = await self.hass.async_add_executor_job(
                lambda: segment_path.exists() and segment_path.stat().st_size > 1024
            )
            if not file_ok:
                elapsed = (datetime.now(timezone.utc) - segment_start).total_seconds()
                _LOGGER.warning(
                    "Segment %d for %s has no content (took %.1fs) — "
                    "camera stream may be unavailable",
                    self._segment_index,
                    self.event_id,
                    elapsed,
                )
                self._segment_index -= 1  # Don't count this segment
                return None

            # Update metadata
            add_segment_to_metadata(
                self.event_data,
                segment_filename,
                self._segment_index,
                self.clip_duration,
            )
            await self.hass.async_add_executor_job(
                save_event_metadata,
                self.media_path, self.camera_name,
                self.event_id, self.event_data,
            )
            # Update the events index so the card can see new segments
            await self.hass.async_add_executor_job(
                append_to_events_index,
                self.media_path, self.camera_name, self.event_data,
            )

            _LOGGER.debug("Segment %d saved: %s", self._segment_index, segment_path)
            return segment_filename

        except Exception:
            _LOGGER.exception(
                "Failed to record segment %d for %s",
                self._segment_index,
                self.event_id,
            )
            self._segment_index -= 1  # Don't count failed segments
            return None

    async def run(self) -> None:
        """Main recording loop — records segments until the event ends."""
        try:
            # Create event directory
            self.event_dir = await self.hass.async_add_executor_job(
                ensure_event_dir,
                self.media_path, self.camera_name, self.event_id,
            )

            # Save initial metadata
            await self.hass.async_add_executor_job(
                save_event_metadata,
                self.media_path, self.camera_name,
                self.event_id, self.event_data,
            )

            # Take snapshot for thumbnail
            await self.take_snapshot()

            # Initial index entry (in_progress)
            await self.hass.async_add_executor_job(
                append_to_events_index,
                self.media_path, self.camera_name, self.event_data,
            )

            # Segment loop
            consecutive_failures = 0
            while self._should_continue():
                result = await self._record_segment()
                if result is None:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        _LOGGER.error(
                            "3 consecutive recording failures for %s — aborting",
                            self.event_id,
                        )
                        fail_event_metadata(
                            self.event_data,
                            "3 consecutive recording failures",
                        )
                        break
                    # Back off with increasing delay to avoid hammering a failing camera
                    retry_delay = min(5 * (2 ** (consecutive_failures - 1)), 30)
                    _LOGGER.warning(
                        "Recording attempt %d failed for %s — retrying in %ds",
                        consecutive_failures,
                        self.event_id,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    consecutive_failures = 0

            # Finalize event
            if self.event_data["status"] != "error":
                complete_event_metadata(self.event_data)

            await self.hass.async_add_executor_job(
                save_event_metadata,
                self.media_path, self.camera_name,
                self.event_id, self.event_data,
            )
            await self.hass.async_add_executor_job(
                append_to_events_index,
                self.media_path, self.camera_name, self.event_data,
            )

            _LOGGER.info(
                "Event %s complete: %d segments recorded",
                self.event_id,
                len(self.event_data["segments"]),
            )

        except asyncio.CancelledError:
            _LOGGER.warning("Recording cancelled for event %s", self.event_id)
            if self.event_data["status"] == "in_progress":
                complete_event_metadata(self.event_data)
                await self.hass.async_add_executor_job(
                    save_event_metadata,
                    self.media_path, self.camera_name,
                    self.event_id, self.event_data,
                )
            raise

        except Exception:
            _LOGGER.exception("Unexpected error in recorder for %s", self.event_id)
            fail_event_metadata(self.event_data, "Unexpected error")
            await self.hass.async_add_executor_job(
                save_event_metadata,
                self.media_path, self.camera_name,
                self.event_id, self.event_data,
            )

    def stop(self) -> None:
        """Force stop the recording (used during teardown)."""
        self._stopped = True
        if self.task and not self.task.done():
            self.task.cancel()
