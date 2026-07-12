"""Reolink HA Sekurity — Core Integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.typing import ConfigType

from .alarm import should_activate_lights, should_notify
from .const import (
    CONF_CAMERA_ENTITY,
    CONF_CAMERA_NAME,
    CONF_CAMERAS,
    CONF_CLIP_DURATION,
    CONF_DASHBOARD_PATH,
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_TIMEOUT,
    CONF_LOOKBACK,
    CONF_MAX_DURATION,
    CONF_MEDIA_PATH,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_NOTIFY_TARGETS,
    CONF_POST_ROLL,
    CONF_SENSOR_DEBOUNCE,
    CONF_TRIGGER_SENSORS,
    CONF_RECORD_SENSORS,
    CONF_ALARM_SENSORS,
    DEFAULT_CLIP_DURATION,
    DEFAULT_LIGHT_TIMEOUT,
    DEFAULT_LOOKBACK,
    DEFAULT_MAX_DURATION,
    DEFAULT_MEDIA_PATH,
    DEFAULT_MERGE_WINDOW,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DEFAULT_POST_ROLL,
    DEFAULT_SENSOR_DEBOUNCE,
    DOMAIN,
    EVENT_TYPE_PRIORITY,
    FULL_ALARM_ENTITY,
    NIGHT_ALARM_ENTITY,
)
from .lights import LightController
from .metadata import (
    MediaPathUnavailable,
    append_to_events_index,
    ensure_camera_dirs,
    load_all_events,
    load_event_metadata,
    load_events_index,
    save_event_metadata,
    get_event_dir,
    get_media_base_path,
    verify_media_path,
)
from .notifications import send_error_notification, send_event_notification
from .recorder import EventRecorder
from .stream_keeper import StreamKeeper

_LOGGER = logging.getLogger(__name__)

# Use a dynamic cache buster so the browser always loads the latest version after an update
import time
FRONTEND_SCRIPT_URL = f"/reolink_ha_sekurity/reolink-ha-sekurity-card.js?v={int(time.time())}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Reolink HA Sekurity — register frontend on load."""
    from homeassistant.components.http import StaticPathConfig

    # Register frontend card immediately on integration load (before config entries)
    # This makes the card available right after HACS install + restart
    frontend_path = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                url_path="/reolink_ha_sekurity",
                path=str(frontend_path),
                cache_headers=True,
            )
        ]
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Reolink HA Sekurity from a config entry."""
    coordinator = ReolinkHaSekurityCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_setup()

    # Automatically register the Lovelace resource
    hass.async_create_task(_async_register_lovelace_resource(hass))

    return True

async def _async_register_lovelace_resource(hass: HomeAssistant):
    """Register the custom card in the Lovelace resource registry."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    import asyncio

    async def _add_resource(*args):
        # We might need to wait for lovelace to be fully loaded
        resources = None
        for _ in range(60):
            lovelace_data = hass.data.get("lovelace")
            if lovelace_data:
                if isinstance(lovelace_data, dict):
                    resources = lovelace_data.get("resources")
                else:
                    resources = getattr(lovelace_data, "resources", None)
                
                if resources and getattr(resources, "loaded", False):
                    break
            await asyncio.sleep(1)
            
        if not resources:
            _LOGGER.warning("Lovelace resources not available. Cannot auto-register card.")
            return
            
        if not hasattr(resources, "async_create_item"):
            _LOGGER.warning("Lovelace resources is in YAML mode. You must manually add the resource to configuration.yaml.")
            return

        # Check if our resource is already registered
        url_base = "/reolink_ha_sekurity/reolink-ha-sekurity-card.js"
        exists = False
        
        for item in resources.async_items():
            if item.get("url", "").startswith(url_base):
                exists = True
                # Update the URL if we changed the cache buster
                if item.get("url") != FRONTEND_SCRIPT_URL and hasattr(resources, "async_update_item"):
                    try:
                        await resources.async_update_item(item["id"], {
                            "res_type": "module",
                            "url": FRONTEND_SCRIPT_URL
                        })
                        _LOGGER.info("Updated Reolink HA Sekurity Lovelace resource URL")
                    except Exception as e:
                        _LOGGER.error("Failed to update Lovelace resource: %s", e)
                break

        if not exists:
            try:
                await resources.async_create_item({
                    "res_type": "module",
                    "url": FRONTEND_SCRIPT_URL
                })
                _LOGGER.info("Registered Reolink HA Sekurity custom card as a Lovelace resource")
            except Exception as e:
                _LOGGER.error("Failed to register Lovelace resource: %s", e)

    if hass.state == CoreState.running:
        hass.async_create_task(_add_resource())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _add_resource)



async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
    if coordinator:
        await coordinator.async_teardown()
    return True


class ReolinkHaSekurityCoordinator:
    """Core coordinator — sets up listeners, manages recordings."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        self.active_events: dict[str, EventRecorder] = {}  # camera_name → recorder
        self._unsub_listeners: list[callback] = []
        self._light_controller: LightController | None = None
        self._stream_keeper: StreamKeeper | None = None
        self._sensor_to_camera: dict[str, str] = {}  # sensor_entity → camera_name
        self._nas_error_notified: bool = False  # Prevents notification spam when NAS is down
        self._cancel_health_check: callback | None = None  # Periodic stuck-recorder check
        self._debounce_handles: dict[str, asyncio.TimerHandle] = {}  # sensor_entity → timer

        # Grace period beyond max_duration before force-killing a recorder
        self.STUCK_RECORDER_GRACE = 60  # seconds

    @property
    def media_path(self) -> str:
        return self.config.get(CONF_MEDIA_PATH, DEFAULT_MEDIA_PATH)

    @property
    def notify_targets(self) -> list[str]:
        return self.config.get(CONF_NOTIFY_TARGETS, [])

    @property
    def night_start(self) -> str:
        return self.config.get(CONF_NIGHT_START, DEFAULT_NIGHT_START)

    @property
    def night_end(self) -> str:
        return self.config.get(CONF_NIGHT_END, DEFAULT_NIGHT_END)

    @property
    def dashboard_path(self) -> str:
        return self.config.get(CONF_DASHBOARD_PATH, "/dashboard-security/security")

    @property
    def cameras(self) -> dict[str, dict]:
        return self.config.get(CONF_CAMERAS, {})

    async def async_setup(self) -> None:
        """Initialize the integration."""
        _LOGGER.warning("[SEKURITY] Setting up Reolink HA Sekurity")
        _LOGGER.warning("[SEKURITY] Config cameras: %s", list(self.cameras.keys()))

        # 1. Forward to switch platform
        self.hass.async_create_task(
            self.hass.config_entries.async_forward_entry_setups(self.entry, ["switch"])
        )

        # 2. Create media directories
        await self.hass.async_add_executor_job(self._create_media_dirs)

        # 3. Set up light controller
        light_entities = self.config.get(CONF_LIGHT_ENTITIES, [])
        light_timeout = self.config.get(CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT)
        self._light_controller = LightController(
            self.hass, light_entities, light_timeout
        )

        # 4. Build sensor → camera mapping and register listeners
        all_sensors = []
        for camera_name, cam_cfg in self.cameras.items():
            record_sensors = cam_cfg.get(CONF_RECORD_SENSORS, cam_cfg.get(CONF_TRIGGER_SENSORS, []))
            _LOGGER.warning(
                "[SEKURITY] Camera '%s': entity=%s, record_sensors=%s",
                camera_name,
                cam_cfg.get(CONF_CAMERA_ENTITY),
                record_sensors,
            )
            for sensor in record_sensors:
                self._sensor_to_camera[sensor] = camera_name
                all_sensors.append(sensor)

        _LOGGER.warning("[SEKURITY] Listening to sensors: %s", all_sensors)

        if all_sensors:
            unsub = async_track_state_change_event(
                self.hass, all_sensors, self._on_sensor_change
            )
            self._unsub_listeners.append(unsub)
        else:
            _LOGGER.warning("[SEKURITY] WARNING: No sensors to monitor!")

        # 5. Register API views
        self.hass.http.register_view(EventsAPIView(self))
        self.hass.http.register_view(EventDetailAPIView(self))
        self.hass.http.register_view(MediaFileView(self))

        _LOGGER.warning(
            "[SEKURITY] Ready — monitoring %d cameras, %d sensors",
            len(self.cameras),
            len(all_sensors),
        )

        # 6. Start periodic health check for stuck recorders (every 5 min)
        self._cancel_health_check = async_track_time_interval(
            self.hass,
            self._periodic_health_check,
            timedelta(minutes=5),
        )
        _LOGGER.info("[SEKURITY] Stuck-recorder health check scheduled (every 5 min)")

        # 7. Start StreamKeeper — keeps RTSP streams alive 24/7
        camera_entities = [
            cam_cfg[CONF_CAMERA_ENTITY]
            for cam_cfg in self.cameras.values()
            if CONF_CAMERA_ENTITY in cam_cfg
        ]
        if camera_entities:
            self._stream_keeper = StreamKeeper(self.hass)
            # Delay stream start slightly to let camera entities finish loading
            async def _start_streams(*_args):
                await self._stream_keeper.async_start(camera_entities)

            from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
            from homeassistant.core import CoreState

            if self.hass.state == CoreState.running:
                self.hass.async_create_task(_start_streams())
            else:
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, _start_streams
                )

    def _create_media_dirs(self) -> None:
        """Create camera directories on the NAS."""
        try:
            for camera_name in self.cameras:
                ensure_camera_dirs(self.media_path, camera_name)
            self._nas_error_notified = False  # NAS is back — reset flag
        except MediaPathUnavailable as exc:
            _LOGGER.error("[SEKURITY] NAS media path unavailable: %s", exc)

    async def _on_sensor_change(self, event: Event) -> None:
        """Handle binary sensor state changes."""
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if new_state is None:
            return

        new_val = new_state.state
        old_val = old_state.state if old_state else None

        # Ignore unavailable/unknown transitions unless a recording is active
        if new_val in ("unavailable", "unknown") and old_val not in ("on", "detected"):
            _LOGGER.debug(
                "[SEKURITY] Ignoring %s -> %s for %s (not actionable)",
                old_val, new_val, entity_id,
            )
            return

        _LOGGER.debug(
            "[SEKURITY] Sensor change: %s -> %s (was: %s)",
            entity_id, new_val, old_val,
        )

        camera_name = self._sensor_to_camera.get(entity_id)
        if camera_name is None:
            _LOGGER.warning("[SEKURITY] Sensor %s not mapped to any camera", entity_id)
            return

        cam_cfg = self.cameras.get(camera_name)
        if cam_cfg is None:
            return

        if new_val in ("on", "detected"):
            _LOGGER.warning("[SEKURITY] DETECTION: %s on %s", entity_id, camera_name)

            # If there's already an active event, skip debounce (re-fire handling)
            if camera_name in self.active_events:
                await self._handle_sensor_on(entity_id, camera_name, cam_cfg)
                return

            # Debounce: wait before starting a new event to filter sub-second blips
            debounce_secs = cam_cfg.get(
                CONF_SENSOR_DEBOUNCE, DEFAULT_SENSOR_DEBOUNCE
            )
            if debounce_secs > 0:
                # Cancel any existing debounce timer for this sensor
                existing = self._debounce_handles.pop(entity_id, None)
                if existing is not None:
                    existing.cancel()

                _LOGGER.info(
                    "[SEKURITY] Debouncing %s for %ds before starting event",
                    entity_id, debounce_secs,
                )
                loop = self.hass.loop
                handle = loop.call_later(
                    debounce_secs,
                    lambda eid=entity_id, cn=camera_name, cc=cam_cfg: (
                        self.hass.async_create_task(
                            self._debounce_fire(eid, cn, cc),
                            f"sekurity_debounce_{eid}",
                        )
                    ),
                )
                self._debounce_handles[entity_id] = handle
            else:
                await self._handle_sensor_on(entity_id, camera_name, cam_cfg)

        elif new_val in ("off", "clear", "unavailable", "unknown") and old_val in ("on", "detected"):
            # Cancel pending debounce if sensor turned off before debounce fired
            pending = self._debounce_handles.pop(entity_id, None)
            if pending is not None:
                pending.cancel()
                _LOGGER.info(
                    "[SEKURITY] Debounce CANCELLED for %s on %s "
                    "(sensor turned off within debounce window — likely false positive)",
                    entity_id, camera_name,
                )
            else:
                await self._handle_sensor_off(entity_id, camera_name)

    async def _debounce_fire(
        self, entity_id: str, camera_name: str, cam_cfg: dict
    ) -> None:
        """Called after debounce timer expires. Start recording if sensor is still on."""
        # Clean up the handle reference
        self._debounce_handles.pop(entity_id, None)

        # Verify the sensor is still on (it could have turned off and back on)
        state = self.hass.states.get(entity_id)
        sensor_val = state.state if state else "unknown"

        if sensor_val in ("on", "detected"):
            _LOGGER.warning(
                "[SEKURITY] Debounce CONFIRMED for %s on %s "
                "(sensor still on after debounce — starting event)",
                entity_id, camera_name,
            )
            await self._handle_sensor_on(entity_id, camera_name, cam_cfg)
        else:
            _LOGGER.info(
                "[SEKURITY] Debounce EXPIRED for %s on %s "
                "(sensor is now %s — skipping event, likely false positive)",
                entity_id, camera_name, sensor_val,
            )

    async def _handle_sensor_on(
        self, entity_id: str, camera_name: str, cam_cfg: dict
    ) -> None:
        """Handle a detection sensor turning ON."""
        if camera_name in self.active_events:
            recorder = self.active_events[camera_name]

            # Check if the existing recorder is stuck
            if recorder.is_stuck(self.STUCK_RECORDER_GRACE):
                _LOGGER.error(
                    "[SEKURITY] STUCK RECORDER detected for %s: "
                    "event=%s has been running for %.0fs "
                    "(max_duration=%ds + grace=%ds). Force-stopping.",
                    camera_name, recorder.event_id,
                    recorder.elapsed_seconds(),
                    recorder.max_duration, self.STUCK_RECORDER_GRACE,
                )
                recorder.stop()
                self.active_events.pop(camera_name, None)
            else:
                # Event already recording — upgrade type if needed and reset merge
                recorder.upgrade_event_type(entity_id)
                recorder.sensor_on_again()
                _LOGGER.info(
                    "[SEKURITY] Sensor %s re-fired during active event %s "
                    "(elapsed=%.0fs, segments=%d)",
                    entity_id, recorder.event_id,
                    recorder.elapsed_seconds(),
                    len(recorder.event_data.get("segments", [])),
                )

                # If this trigger is an alarm sensor and notification hasn't been sent, notify now
                alarm_sensors = cam_cfg.get(CONF_ALARM_SENSORS, cam_cfg.get(CONF_TRIGGER_SENSORS, []))
                is_alarm_sensor = entity_id in alarm_sensors

                if is_alarm_sensor and not recorder.event_data.get("notification_sent"):
                    if should_notify(self.hass, True, self.night_start, self.night_end):
                        recorder.event_data["alarm_active"] = True
                        recorder.event_data["notification_sent"] = True
                        self.hass.async_create_task(
                            send_event_notification(
                                self.hass,
                                recorder.event_data,
                                self.notify_targets,
                                self.dashboard_path,
                            )
                        )
                    if should_activate_lights(self.hass, True, self.night_start, self.night_end):
                        recorder.event_data["lights_activated"] = True
                        if self._light_controller:
                            self.hass.async_create_task(
                                self._light_controller.activate()
                            )
                return

        # Verify NAS is available before starting a new recording
        try:
            await self.hass.async_add_executor_job(
                verify_media_path, self.media_path
            )
        except MediaPathUnavailable as exc:
            _LOGGER.error(
                "[SEKURITY] Cannot start recording for %s — NAS unavailable: %s",
                camera_name,
                exc,
            )
            if not self._nas_error_notified and self.notify_targets:
                self._nas_error_notified = True
                await send_error_notification(
                    self.hass,
                    self.notify_targets,
                    camera_name,
                    f"NAS media storage is unavailable. Recordings are NOT being saved. "
                    f"Check Settings → System → Storage.",
                )
            return

        # NAS is reachable — clear the error flag
        self._nas_error_notified = False

        # Ensure camera stream is warm before recording
        if self._stream_keeper:
            await self._stream_keeper.ensure_stream_ready(
                cam_cfg[CONF_CAMERA_ENTITY]
            )

        # Start a new event
        event_type = EventRecorder._detect_event_type(self.hass, entity_id)
        _LOGGER.info(
            "New event: %s detected on %s (sensor: %s)",
            event_type,
            camera_name,
            entity_id,
        )

        recorder = EventRecorder(
            hass=self.hass,
            camera_entity=cam_cfg[CONF_CAMERA_ENTITY],
            camera_name=camera_name,
            trigger_entity=entity_id,
            event_type=event_type,
            media_path=self.media_path,
            clip_duration=cam_cfg.get(CONF_CLIP_DURATION, DEFAULT_CLIP_DURATION),
            max_duration=cam_cfg.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION),
            lookback=cam_cfg.get(CONF_LOOKBACK, DEFAULT_LOOKBACK),
            post_roll=cam_cfg.get(CONF_POST_ROLL, DEFAULT_POST_ROLL),
            stream_keeper=self._stream_keeper,
        )
        self.active_events[camera_name] = recorder

        _LOGGER.info(
            "[SEKURITY] Launching recording task for %s "
            "(event=%s, active_cameras=%d)",
            camera_name, recorder.event_id, len(self.active_events),
        )

        # Launch recording as background task (must not block HA startup)
        recorder.task = self.hass.async_create_background_task(
            self._run_and_cleanup(camera_name, recorder),
            name=f"sekurity_record_{camera_name}",
        )

        # Evaluate alarm (non-blocking — don't delay recording start)
        alarm_sensors = cam_cfg.get(CONF_ALARM_SENSORS, cam_cfg.get(CONF_TRIGGER_SENSORS, []))
        is_alarm_sensor = entity_id in alarm_sensors

        if is_alarm_sensor and should_notify(
            self.hass, True, self.night_start, self.night_end
        ):
            recorder.event_data["alarm_active"] = True
            recorder.event_data["notification_sent"] = True
            # Send notification immediately (before first segment finishes)
            self.hass.async_create_task(
                send_event_notification(
                    self.hass,
                    recorder.event_data,
                    self.notify_targets,
                    self.dashboard_path,
                )
            )

        if is_alarm_sensor and should_activate_lights(
            self.hass, True, self.night_start, self.night_end
        ):
            recorder.event_data["lights_activated"] = True
            if self._light_controller:
                self.hass.async_create_task(
                    self._light_controller.activate()
                )

    async def _handle_sensor_off(
        self, entity_id: str, camera_name: str
    ) -> None:
        """Handle a detection sensor turning OFF."""
        if camera_name in self.active_events:
            recorder = self.active_events[camera_name]
            # Check if ALL trigger sensors for this camera are off
            cam_cfg = self.cameras.get(camera_name, {})
            record_sensors = cam_cfg.get(CONF_RECORD_SENSORS, cam_cfg.get(CONF_TRIGGER_SENSORS, []))
            any_still_on = False
            for sensor in record_sensors:
                state = self.hass.states.get(sensor)
                if state and state.state in ("on", "detected"):
                    any_still_on = True
                    break

            if not any_still_on:
                recorder.sensor_off()
                _LOGGER.debug(
                    "All sensors off for %s — post-roll started", camera_name
                )

    async def _run_and_cleanup(
        self, camera_name: str, recorder: EventRecorder
    ) -> None:
        """Run the recorder and clean up when done."""
        try:
            await recorder.run()
        except MediaPathUnavailable as exc:
            _LOGGER.error(
                "[SEKURITY] Recording failed for %s — NAS unavailable: %s",
                camera_name, exc,
            )
            if not self._nas_error_notified and self.notify_targets:
                self._nas_error_notified = True
                await send_error_notification(
                    self.hass,
                    self.notify_targets,
                    camera_name,
                    f"NAS media storage became unavailable during recording. "
                    f"Check Settings → System → Storage.",
                )
        except Exception:
            _LOGGER.exception(
                "[SEKURITY] Recording FAILED for %s (event=%s)",
                camera_name, recorder.event_id,
            )
            if not self._nas_error_notified and self.notify_targets:
                self._nas_error_notified = True
                await send_error_notification(
                    self.hass,
                    self.notify_targets,
                    camera_name,
                    f"Recording failed for camera {camera_name}",
                )
        finally:
            self.active_events.pop(camera_name, None)
            _LOGGER.info(
                "[SEKURITY] Cleaned up event for %s "
                "(remaining active: %d cameras: %s)",
                camera_name, len(self.active_events),
                list(self.active_events.keys()),
            )

    async def _periodic_health_check(self, now=None) -> None:
        """Periodically check for stuck recorders and force-stop them."""
        if not self.active_events:
            return

        _LOGGER.debug(
            "[SEKURITY] Health check: %d active events: %s",
            len(self.active_events),
            {name: f"{rec.event_id} ({rec.elapsed_seconds():.0f}s)"
             for name, rec in self.active_events.items()},
        )

        stuck_cameras = []
        for camera_name, recorder in list(self.active_events.items()):
            if recorder.is_stuck(self.STUCK_RECORDER_GRACE):
                stuck_cameras.append((camera_name, recorder))

        for camera_name, recorder in stuck_cameras:
            _LOGGER.error(
                "[SEKURITY] HEALTH CHECK: Force-stopping stuck recorder "
                "for %s: event=%s, elapsed=%.0fs, max_duration=%ds, "
                "segments=%d, entity=%s",
                camera_name, recorder.event_id,
                recorder.elapsed_seconds(), recorder.max_duration,
                len(recorder.event_data.get("segments", [])),
                recorder.camera_entity,
            )
            recorder.stop()
            self.active_events.pop(camera_name, None)

            # Notify if configured
            if self.notify_targets:
                await send_error_notification(
                    self.hass,
                    self.notify_targets,
                    camera_name,
                    f"Stuck recording detected and cleaned up for {camera_name}. "
                    f"Event {recorder.event_id} was running for "
                    f"{recorder.elapsed_seconds()/60:.0f} minutes.",
                )

    async def async_teardown(self) -> None:
        """Clean up on unload."""
        _LOGGER.info(
            "[SEKURITY] Tearing down: %d active events, %d listeners",
            len(self.active_events), len(self._unsub_listeners),
        )

        # Cancel periodic health check
        if self._cancel_health_check:
            self._cancel_health_check()
            self._cancel_health_check = None

        # Cancel all state listeners
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

        # Stop all active recordings
        for name, recorder in self.active_events.items():
            _LOGGER.info("[SEKURITY] Stopping active recorder: %s", name)
            recorder.stop()
        self.active_events.clear()

        # Stop stream keeper
        if self._stream_keeper:
            self._stream_keeper.stop()

        # Cancel light timer
        if self._light_controller:
            self._light_controller.cancel()


# --- REST API Views for the Frontend Card ---


class EventsAPIView(HomeAssistantView):
    """API endpoint to list events for the frontend card."""

    url = "/api/reolink_ha_sekurity/events"
    name = "api:reolink_ha_sekurity:events"
    requires_auth = True

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request):
        """Handle GET request — return events list."""
        from aiohttp import web

        params = request.query
        camera = params.get("camera", None)
        limit = int(params.get("limit", 25))
        offset = int(params.get("offset", 0))

        filter_type = params.get("filter", "security")

        if camera and camera != "all":
            events = await self._coordinator.hass.async_add_executor_job(
                load_events_index,
                self._coordinator.media_path,
                camera,
            )
        else:
            camera_names = list(self._coordinator.cameras.keys())
            events = await self._coordinator.hass.async_add_executor_job(
                load_all_events,
                self._coordinator.media_path,
                camera_names,
            )

        if filter_type == "security":
            events = [e for e in events if e.get("alarm_active", False)]
            
        events = events[offset : offset + limit]

        # Sign snapshot URLs for event list thumbnails
        from homeassistant.components.http.auth import async_sign_path
        from datetime import timedelta

        for ev in events:
            if ev.get("snapshot"):
                raw_url = f"/api/reolink_ha_sekurity/media/{ev['camera']}/{ev['event_id']}/{ev['snapshot']}"
                ev["snapshot_url"] = async_sign_path(
                    self._coordinator.hass,
                    raw_url,
                    timedelta(hours=1),
                )

        # Include active events info
        active = {
            name: {
                "event_id": rec.event_id,
                "event_type": rec.event_data.get("event_type", ""),
                "camera_entity": rec.camera_entity,
                "started_at": rec.event_data.get("started_at", ""),
                "segment_count": len(rec.event_data.get("segments", [])),
            }
            for name, rec in self._coordinator.active_events.items()
        }

        return web.json_response(
            {
                "events": events,
                "active_events": active,
                "cameras": list(self._coordinator.cameras.keys()),
            }
        )


class EventDetailAPIView(HomeAssistantView):
    """API endpoint to get event detail including segment file paths."""

    url = "/api/reolink_ha_sekurity/event/{event_id}"
    name = "api:reolink_ha_sekurity:event_detail"
    requires_auth = True

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request, event_id: str):
        """Handle GET request — return event metadata with media URLs."""
        from aiohttp import web

        # Find which camera this event belongs to
        camera_name = None
        parts = event_id.split("_")
        if len(parts) >= 3:
            # event_id format: YYYYMMDD_HHMMSS_cameraname
            camera_name = "_".join(parts[2:])

        if camera_name is None:
            return web.json_response({"error": "Invalid event ID"}, status=400)

        metadata = await self._coordinator.hass.async_add_executor_job(
            load_event_metadata,
            self._coordinator.media_path,
            camera_name,
            event_id,
        )

        if metadata is None:
            return web.json_response({"error": "Event not found"}, status=404)

        # Sign media URLs so <img>/<video> tags work without extra auth
        from homeassistant.components.http.auth import async_sign_path
        from datetime import timedelta

        base_media_url = f"/api/reolink_ha_sekurity/media/{camera_name}/{event_id}"

        segments_with_urls = []
        for seg in metadata.get("segments", []):
            raw_url = f"{base_media_url}/{seg['file']}"
            signed = async_sign_path(
                self._coordinator.hass,
                raw_url,
                timedelta(hours=1),
            )
            segments_with_urls.append(
                {
                    **seg,
                    "url": signed,
                }
            )

        snapshot_url = None
        if metadata.get("snapshot"):
            raw_snap = f"{base_media_url}/{metadata['snapshot']}"
            snapshot_url = async_sign_path(
                self._coordinator.hass,
                raw_snap,
                timedelta(hours=1),
            )

        # Check if this is an active event
        is_active = camera_name in self._coordinator.active_events
        camera_entity = metadata.get("camera_entity", "")

        return web.json_response(
            {
                "metadata": metadata,
                "segments": segments_with_urls,
                "snapshot_url": snapshot_url,
                "is_active": is_active,
                "camera_entity": camera_entity,
            }
        )

class MediaFileView(HomeAssistantView):
    """Serve media files (segments, snapshots) with HA authentication."""

    url = "/api/reolink_ha_sekurity/media/{camera_name}/{event_id}/{filename}"
    name = "api:reolink_ha_sekurity:media"
    requires_auth = True  # HA middleware validates signed URLs automatically

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request, camera_name: str, event_id: str, filename: str):
        """Serve a media file."""
        from aiohttp import web
        import mimetypes

        # Sanitize inputs to prevent path traversal
        for part in (camera_name, event_id, filename):
            if ".." in part or "/" in part or "\\" in part:
                return web.Response(status=400, text="Invalid path")

        media_base = get_media_base_path(self._coordinator.media_path)
        file_path = media_base / camera_name / event_id / filename

        if not file_path.exists():
            return web.Response(status=404, text="File not found")

        # Determine content type
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        return web.FileResponse(
            file_path,
            headers={"Content-Type": content_type},
        )
