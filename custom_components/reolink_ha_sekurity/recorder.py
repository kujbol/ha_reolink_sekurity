"""Continuous segmented recording engine for Reolink HA Sekurity."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .stream_keeper import StreamKeeper

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

    # Timeout margin added to clip_duration for camera.record calls
    RECORD_TIMEOUT_MARGIN = 30  # seconds

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
        stream_keeper: StreamKeeper | None = None,
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
        self._stream_keeper = stream_keeper
        self.started_at = datetime.now(timezone.utc)

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

        _LOGGER.info(
            "[SEKURITY] EventRecorder created: event=%s camera=%s entity=%s "
            "type=%s clip=%ds max=%ds lookback=%ds post_roll=%ds",
            self.event_id, camera_name, camera_entity,
            event_type, clip_duration, max_duration, lookback, post_roll,
        )

    @property
    def is_running(self) -> bool:
        """Check if the recorder is still running."""
        return self.task is not None and not self.task.done()

    def sensor_off(self) -> None:
        """Signal that the trigger sensor has turned off."""
        if self._sensor_on:
            self._sensor_on = False
            self._sensor_off_time = datetime.now(timezone.utc)
            elapsed = self.elapsed_seconds()
            _LOGGER.info(
                "[SEKURITY] Sensor OFF for event %s (elapsed=%.0fs, segments=%d) "
                "— entering post-roll (%ds) + merge window (%ds)",
                self.event_id, elapsed, self._segment_index,
                self.post_roll, self.merge_window,
            )

    def sensor_on_again(self) -> None:
        """Signal that the trigger sensor has turned back on (within merge window)."""
        self._sensor_on = True
        self._sensor_off_time = None
        _LOGGER.info(
            "[SEKURITY] Sensor RE-ACTIVATED for event %s (elapsed=%.0fs)",
            self.event_id, self.elapsed_seconds(),
        )

    def elapsed_seconds(self) -> float:
        """Return seconds elapsed since the recorder was created."""
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

    def is_stuck(self, grace_seconds: int = 60) -> bool:
        """Check if this recorder has exceeded max_duration + grace period."""
        return self.elapsed_seconds() > (self.max_duration + grace_seconds)

    def upgrade_event_type(self, trigger_entity: str) -> None:
        """Upgrade event type if a higher-priority detection fires."""
        new_type = self._detect_event_type(self.hass, trigger_entity)
        current_priority = EVENT_TYPE_PRIORITY.get(
            self.event_data["event_type"], 0
        )
        new_priority = EVENT_TYPE_PRIORITY.get(new_type, 0)

        if new_priority > current_priority:
            upgrade_time = datetime.now(timezone.utc).astimezone()
            _LOGGER.info(
                "[SEKURITY] Upgrading event %s from %s to %s "
                "(%.0fs after recording start, trigger=%s)",
                self.event_id,
                self.event_data["event_type"],
                new_type,
                self.elapsed_seconds(),
                trigger_entity,
            )
            self.event_data["event_type"] = new_type
            self.event_data["trigger_entity"] = trigger_entity
            self.event_data["type_upgraded_at"] = upgrade_time.isoformat()

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
            _LOGGER.debug(
                "[SEKURITY] _should_continue=False: stopped flag set for %s",
                self.event_id,
            )
            return False

        # Enforce max duration to prevent infinite recordings
        elapsed_total = self.elapsed_seconds()

        if elapsed_total >= self.max_duration:
            _LOGGER.warning(
                "[SEKURITY] Event %s reached max_duration (%ds, elapsed=%.0fs). "
                "Forcing stop after %d segments.",
                self.event_id, self.max_duration, elapsed_total,
                self._segment_index,
            )
            return False

        if self._sensor_on:
            return True

        # Sensor is off — check if we're still in post-roll or merge window
        if self._sensor_off_time is None:
            _LOGGER.debug(
                "[SEKURITY] _should_continue=False: sensor off, no off_time for %s",
                self.event_id,
            )
            return False

        elapsed_since_off = (
            datetime.now(timezone.utc) - self._sensor_off_time
        ).total_seconds()

        # Keep recording during post-roll period
        if elapsed_since_off < self.post_roll:
            return True

        # After post-roll, wait for merge window
        if elapsed_since_off < self.post_roll + self.merge_window:
            return True

        _LOGGER.info(
            "[SEKURITY] _should_continue=False: post-roll+merge expired for %s "
            "(%.0fs since sensor off, post_roll=%d, merge=%d)",
            self.event_id, elapsed_since_off, self.post_roll, self.merge_window,
        )
        return False

    async def take_snapshot(self) -> str | None:
        """Take a camera snapshot for the event thumbnail.

        Retries a few times because Reolink cameras often can't serve a
        snapshot while the RTSP stream is starting up for recording.
        """
        if self.event_dir is None:
            return None

        snapshot_filename = "snapshot.jpg"
        snapshot_path = self.event_dir / snapshot_filename

        from homeassistant.components.camera import async_get_image

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                image = await async_get_image(self.hass, self.camera_entity)
                if image and image.content:
                    def write_image():
                        with open(snapshot_path, "wb") as f:
                            f.write(image.content)

                    await self.hass.async_add_executor_job(write_image)

                    self.event_data["snapshot"] = snapshot_filename
                    _LOGGER.debug("Snapshot saved on attempt %d: %s", attempt, snapshot_path)
                    return snapshot_filename
            except Exception:
                if attempt < max_attempts:
                    _LOGGER.debug(
                        "Snapshot attempt %d/%d failed for %s — retrying in 2s",
                        attempt,
                        max_attempts,
                        self.event_id,
                    )
                    await asyncio.sleep(2)
                else:
                    _LOGGER.debug(
                        "All %d snapshot attempts failed for %s — "
                        "will try to extract from first segment",
                        max_attempts,
                        self.event_id,
                    )

        return None

    async def _extract_thumbnail_from_segment(self, segment_path: Path) -> str | None:
        """Extract a thumbnail frame from a recorded mp4 segment using ffmpeg.

        Used as a fallback when camera snapshot is unavailable.
        """
        if self.event_dir is None:
            return None

        snapshot_filename = "snapshot.jpg"
        snapshot_path = self.event_dir / snapshot_filename

        try:
            # Extract a frame from 1 second into the video
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-ss", "1",
                "-i", str(segment_path),
                "-frames:v", "1",
                "-q:v", "2",
                "-y",
                str(snapshot_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)

            if proc.returncode == 0 and snapshot_path.exists() and snapshot_path.stat().st_size > 0:
                self.event_data["snapshot"] = snapshot_filename
                _LOGGER.info(
                    "Thumbnail extracted from segment for %s", self.event_id
                )
                return snapshot_filename

            _LOGGER.warning(
                "ffmpeg thumbnail extraction failed (rc=%s) for %s",
                proc.returncode,
                self.event_id,
            )
            return None

        except FileNotFoundError:
            _LOGGER.debug("ffmpeg not available — skipping thumbnail extraction")
            return None
        except asyncio.TimeoutError:
            _LOGGER.warning("ffmpeg thumbnail extraction timed out for %s", self.event_id)
            return None
        except Exception:
            _LOGGER.exception("Thumbnail extraction error for %s", self.event_id)
            return None

    async def _record_segment(self, is_retry: bool = False) -> str | None:
        """Record a single segment. Returns the segment filename or None on failure."""
        self._segment_index += 1
        segment_filename = f"{self.event_id}_seg{self._segment_index:03d}.mp4"
        segment_path = self.event_dir / segment_filename

        # First segment uses full lookback; subsequent segments use overlap
        current_lookback = (
            self.lookback if self._segment_index == 1 else DEFAULT_SEGMENT_OVERLAP
        )

        if is_retry:
            _LOGGER.info(
                "[SEKURITY] Retrying segment %d without lookback for %s",
                self._segment_index, self.event_id,
            )
            current_lookback = 0

        # Track timing to detect when camera.record returns too fast (stream failure)
        segment_start = datetime.now(timezone.utc)

        # Timeout = clip_duration + margin to prevent infinite hangs
        record_timeout = self.clip_duration + self.RECORD_TIMEOUT_MARGIN

        try:
            _LOGGER.info(
                "[SEKURITY] Recording segment %d for %s "
                "(duration=%ds, lookback=%ds, timeout=%ds, total_elapsed=%.0fs)",
                self._segment_index, self.event_id,
                self.clip_duration, current_lookback, record_timeout,
                self.elapsed_seconds(),
            )
            service_data = {
                "entity_id": self.camera_entity,
                "filename": str(segment_path),
                "duration": self.clip_duration,
            }
            if current_lookback > 0:
                service_data["lookback"] = current_lookback

            # Pre-check: verify camera is available before attempting to record
            cam_state = self.hass.states.get(self.camera_entity)
            cam_state_str = cam_state.state if cam_state else "not_found"
            stream_alive = (
                self._stream_keeper.is_stream_alive(self.camera_entity)
                if self._stream_keeper else "no_keeper"
            )
            _LOGGER.info(
                "[SEKURITY] Pre-record check for %s seg %d: "
                "cam_state=%s, stream_alive=%s",
                self.event_id, self._segment_index,
                cam_state_str, stream_alive,
            )

            if cam_state is not None and cam_state.state == "unavailable":
                _LOGGER.warning(
                    "[SEKURITY] Camera %s is UNAVAILABLE — skipping segment %d "
                    "for %s (will retry after stream recovery)",
                    self.camera_entity, self._segment_index, self.event_id,
                )
                self._segment_index -= 1
                return None

            try:
                await asyncio.wait_for(
                    self.hass.services.async_call(
                        "camera",
                        "record",
                        service_data,
                        blocking=True,
                    ),
                    timeout=record_timeout,
                )
            except asyncio.TimeoutError:
                elapsed = (datetime.now(timezone.utc) - segment_start).total_seconds()
                _LOGGER.error(
                    "[SEKURITY] camera.record TIMED OUT for segment %d of %s "
                    "after %.1fs (timeout=%ds) — camera stream likely hung. "
                    "Entity: %s",
                    self._segment_index, self.event_id,
                    elapsed, record_timeout, self.camera_entity,
                )
                self._segment_index -= 1
                return None

            segment_elapsed = (datetime.now(timezone.utc) - segment_start).total_seconds()

            # Verify the file was actually created with content.
            # camera.record can return without error but fail to capture
            # anything (HA stream logs "Recording failed to capture anything"
            # internally without raising).
            file_ok = await self.hass.async_add_executor_job(
                lambda: segment_path.exists() and segment_path.stat().st_size > 1024
            )
            if not file_ok:
                # Gather extra diagnostics
                file_exists = await self.hass.async_add_executor_job(
                    lambda: segment_path.exists()
                )
                file_size_bytes = 0
                if file_exists:
                    file_size_bytes = await self.hass.async_add_executor_job(
                        lambda: segment_path.stat().st_size
                    )
                cam_state_after = self.hass.states.get(self.camera_entity)
                _LOGGER.warning(
                    "[SEKURITY] Segment %d for %s has NO CONTENT "
                    "(took %.1fs, expected ~%ds) — camera stream may be "
                    "unavailable. Entity: %s, Path: %s, "
                    "file_exists=%s, file_size=%d bytes, "
                    "cam_state_after=%s",
                    self._segment_index, self.event_id,
                    segment_elapsed, self.clip_duration,
                    self.camera_entity, segment_path,
                    file_exists, file_size_bytes,
                    cam_state_after.state if cam_state_after else "not_found",
                )
                self._segment_index -= 1  # Don't count this segment
                return None

            # Log file size for debugging
            file_size = await self.hass.async_add_executor_job(
                lambda: segment_path.stat().st_size if segment_path.exists() else 0
            )

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

            _LOGGER.info(
                "[SEKURITY] Segment %d SAVED for %s: %s "
                "(%.1fs, %d bytes, total_elapsed=%.0fs)",
                self._segment_index, self.event_id, segment_filename,
                segment_elapsed, file_size, self.elapsed_seconds(),
            )
            return segment_filename

        except asyncio.CancelledError:
            _LOGGER.warning(
                "[SEKURITY] Segment %d CANCELLED for %s (total_elapsed=%.0fs)",
                self._segment_index, self.event_id, self.elapsed_seconds(),
            )
            self._segment_index -= 1
            raise

        except Exception:
            _LOGGER.exception(
                "[SEKURITY] FAILED to record segment %d for %s "
                "(total_elapsed=%.0fs, entity=%s)",
                self._segment_index, self.event_id,
                self.elapsed_seconds(), self.camera_entity,
            )
            self._segment_index -= 1  # Don't count failed segments
            return None

    async def run(self) -> None:
        """Main recording loop — records segments until the event ends."""
        _LOGGER.info(
            "[SEKURITY] Recording STARTED for event %s on %s (entity=%s)",
            self.event_id, self.camera_name, self.camera_entity,
        )
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
                # Safety: check elapsed time BEFORE starting a new segment
                elapsed = self.elapsed_seconds()
                if elapsed >= self.max_duration:
                    _LOGGER.warning(
                        "[SEKURITY] Event %s exceeded max_duration (%ds) "
                        "before segment start (elapsed=%.0fs). Stopping.",
                        self.event_id, self.max_duration, elapsed,
                    )
                    break

                result = await self._record_segment(is_retry=(consecutive_failures > 0))
                if result is None:
                    consecutive_failures += 1
                    _LOGGER.warning(
                        "[SEKURITY] Segment failure #%d for %s "
                        "(total_elapsed=%.0fs)",
                        consecutive_failures, self.event_id,
                        self.elapsed_seconds(),
                    )
                    if consecutive_failures >= 3:
                        _LOGGER.error(
                            "[SEKURITY] 3 CONSECUTIVE recording failures for %s "
                            "— aborting event (elapsed=%.0fs, entity=%s)",
                            self.event_id, self.elapsed_seconds(),
                            self.camera_entity,
                        )
                        fail_event_metadata(
                            self.event_data,
                            "3 consecutive recording failures",
                        )
                        break

                    # Try to re-warm the stream before retrying
                    if self._stream_keeper:
                        _LOGGER.info(
                            "[SEKURITY] Re-warming stream for %s before retry %d",
                            self.camera_entity,
                            consecutive_failures + 1,
                        )
                        await self._stream_keeper.ensure_stream_ready(
                            self.camera_entity
                        )

                    # First retry: immediate (stream was just re-warmed)
                    # Subsequent retries: back off to avoid hammering
                    if consecutive_failures > 1:
                        retry_delay = min(5 * (2 ** (consecutive_failures - 2)), 15)
                        _LOGGER.warning(
                            "[SEKURITY] Recording attempt %d failed for %s "
                            "— retrying in %ds",
                            consecutive_failures, self.event_id, retry_delay,
                        )
                        await asyncio.sleep(retry_delay)
                    else:
                        _LOGGER.warning(
                            "[SEKURITY] Recording attempt 1 failed for %s "
                            "— retrying immediately",
                            self.event_id,
                        )
                else:
                    consecutive_failures = 0

                    # After first successful segment, extract thumbnail if
                    # the camera snapshot failed earlier
                    if (
                        self._segment_index == 1
                        and self.event_data.get("snapshot") is None
                        and self.event_dir is not None
                    ):
                        segment_path = self.event_dir / result
                        thumbnail = await self._extract_thumbnail_from_segment(
                            segment_path
                        )
                        if thumbnail:
                            # Update metadata & index with new snapshot
                            await self.hass.async_add_executor_job(
                                save_event_metadata,
                                self.media_path, self.camera_name,
                                self.event_id, self.event_data,
                            )
                            await self.hass.async_add_executor_job(
                                append_to_events_index,
                                self.media_path, self.camera_name,
                                self.event_data,
                            )

            # Finalize event
            if self.event_data["status"] != "error":
                if len(self.event_data["segments"]) == 0:
                    _LOGGER.warning(
                        "[SEKURITY] Event %s completed with 0 segments "
                        "— marking as error (elapsed=%.0fs, entity=%s)",
                        self.event_id, self.elapsed_seconds(),
                        self.camera_entity,
                    )
                    fail_event_metadata(
                        self.event_data,
                        "No segments recorded — camera stream may have been unavailable",
                    )
                else:
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
                "[SEKURITY] Event %s FINISHED: %d segments, "
                "duration=%.0fs, status=%s, error=%s",
                self.event_id,
                len(self.event_data["segments"]),
                self.elapsed_seconds(),
                self.event_data["status"],
                self.event_data.get("error"),
            )

        except asyncio.CancelledError:
            _LOGGER.warning(
                "[SEKURITY] Recording CANCELLED for event %s "
                "(elapsed=%.0fs, segments=%d)",
                self.event_id, self.elapsed_seconds(), self._segment_index,
            )
            if self.event_data["status"] == "in_progress":
                complete_event_metadata(self.event_data)
                await self.hass.async_add_executor_job(
                    save_event_metadata,
                    self.media_path, self.camera_name,
                    self.event_id, self.event_data,
                )
            raise

        except Exception:
            _LOGGER.exception(
                "[SEKURITY] UNEXPECTED ERROR in recorder for %s "
                "(elapsed=%.0fs, segments=%d, entity=%s)",
                self.event_id, self.elapsed_seconds(),
                self._segment_index, self.camera_entity,
            )
            fail_event_metadata(self.event_data, "Unexpected error")
            await self.hass.async_add_executor_job(
                save_event_metadata,
                self.media_path, self.camera_name,
                self.event_id, self.event_data,
            )

    def stop(self) -> None:
        """Force stop the recording (used during teardown)."""
        _LOGGER.info(
            "[SEKURITY] FORCE STOP requested for event %s "
            "(elapsed=%.0fs, segments=%d)",
            self.event_id, self.elapsed_seconds(), self._segment_index,
        )
        self._stopped = True
        if self.task and not self.task.done():
            self.task.cancel()
