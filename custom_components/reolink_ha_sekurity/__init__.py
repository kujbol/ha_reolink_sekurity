"""Reolink HA Sekurity — Core Integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType

from .alarm import should_activate_lights, should_notify
from .const import (
    CONF_ALARM_PARTICIPATION,
    CONF_CAMERA_ENTITY,
    CONF_CAMERA_NAME,
    CONF_CAMERAS,
    CONF_CLIP_DURATION,
    CONF_DASHBOARD_PATH,
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_TIMEOUT,
    CONF_LOOKBACK,
    CONF_MEDIA_PATH,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_NOTIFY_TARGETS,
    CONF_POST_ROLL,
    CONF_TRIGGER_SENSORS,
    DEFAULT_CLIP_DURATION,
    DEFAULT_LIGHT_TIMEOUT,
    DEFAULT_LOOKBACK,
    DEFAULT_MEDIA_PATH,
    DEFAULT_MERGE_WINDOW,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DEFAULT_POST_ROLL,
    DOMAIN,
    EVENT_TYPE_PRIORITY,
    FULL_ALARM_ENTITY,
    NIGHT_ALARM_ENTITY,
)
from .lights import LightController
from .metadata import (
    append_to_events_index,
    ensure_camera_dirs,
    load_all_events,
    load_event_metadata,
    load_events_index,
    save_event_metadata,
    get_event_dir,
    get_media_base_path,
)
from .notifications import send_error_notification, send_event_notification
from .recorder import EventRecorder

_LOGGER = logging.getLogger(__name__)

FRONTEND_SCRIPT_URL = "/reolink_ha_sekurity/reolink-ha-sekurity-card.js"


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
                cache_headers=False,
            )
        ]
    )
    hass.data.setdefault("frontend_extra_module_url", set())
    hass.data["frontend_extra_module_url"].add(FRONTEND_SCRIPT_URL)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Reolink HA Sekurity from a config entry."""
    coordinator = ReolinkHaSekurityCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_setup()
    return True


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
        self._sensor_to_camera: dict[str, str] = {}  # sensor_entity → camera_name

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
        return self.config.get(CONF_DASHBOARD_PATH, "/lovelace/security")

    @property
    def cameras(self) -> dict[str, dict]:
        return self.config.get(CONF_CAMERAS, {})

    async def async_setup(self) -> None:
        """Initialize the integration."""
        _LOGGER.warning("[SEKURITY] Setting up Reolink HA Sekurity")
        _LOGGER.warning("[SEKURITY] Config cameras: %s", list(self.cameras.keys()))

        # 1. Create input_boolean entities for alarm toggles
        await self._create_alarm_toggles()

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
            _LOGGER.warning(
                "[SEKURITY] Camera '%s': entity=%s, sensors=%s",
                camera_name,
                cam_cfg.get(CONF_CAMERA_ENTITY),
                cam_cfg.get(CONF_TRIGGER_SENSORS, []),
            )
            for sensor in cam_cfg.get(CONF_TRIGGER_SENSORS, []):
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

    async def _create_alarm_toggles(self) -> None:
        """Create input_boolean entities for alarm toggles if they don't exist."""
        for entity_id, name in [
            (FULL_ALARM_ENTITY, "Sekurity Full Alarm"),
            (NIGHT_ALARM_ENTITY, "Sekurity Night Alarm"),
        ]:
            if self.hass.states.get(entity_id) is None:
                object_id = entity_id.replace("input_boolean.", "")
                try:
                    await self.hass.services.async_call(
                        "input_boolean",
                        "create",
                        {"name": name, "icon": "mdi:shield-home"},
                        blocking=True,
                    )
                except Exception:
                    # input_boolean.create may not exist — set state directly
                    self.hass.states.async_set(entity_id, "off", {
                        "friendly_name": name,
                        "icon": "mdi:shield-home",
                    })
                    _LOGGER.info("Created alarm toggle state: %s", entity_id)

    def _create_media_dirs(self) -> None:
        """Create camera directories on the NAS."""
        for camera_name in self.cameras:
            ensure_camera_dirs(self.media_path, camera_name)

    async def _on_sensor_change(self, event: Event) -> None:
        """Handle binary sensor state changes."""
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        _LOGGER.warning(
            "[SEKURITY] Sensor change: %s -> %s (was: %s)",
            entity_id,
            new_state.state if new_state else None,
            old_state.state if old_state else None,
        )

        if new_state is None:
            return

        camera_name = self._sensor_to_camera.get(entity_id)
        if camera_name is None:
            _LOGGER.warning("[SEKURITY] Sensor %s not mapped to any camera", entity_id)
            return

        cam_cfg = self.cameras.get(camera_name)
        if cam_cfg is None:
            return

        state_value = new_state.state

        if state_value in ("on", "detected"):
            _LOGGER.warning("[SEKURITY] DETECTION: %s on %s", entity_id, camera_name)
            await self._handle_sensor_on(entity_id, camera_name, cam_cfg)
        elif state_value in ("off", "clear") and old_state and old_state.state in ("on", "detected"):
            await self._handle_sensor_off(entity_id, camera_name)

    async def _handle_sensor_on(
        self, entity_id: str, camera_name: str, cam_cfg: dict
    ) -> None:
        """Handle a detection sensor turning ON."""
        if camera_name in self.active_events:
            # Event already recording — upgrade type if needed and reset merge
            recorder = self.active_events[camera_name]
            recorder.upgrade_event_type(entity_id)
            recorder.sensor_on_again()
            _LOGGER.debug(
                "Sensor %s re-fired during active event %s",
                entity_id,
                recorder.event_id,
            )
            return

        # Start a new event
        event_type = EventRecorder._detect_event_type(entity_id)
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
            lookback=cam_cfg.get(CONF_LOOKBACK, DEFAULT_LOOKBACK),
            post_roll=cam_cfg.get(CONF_POST_ROLL, DEFAULT_POST_ROLL),
        )
        self.active_events[camera_name] = recorder

        # Launch recording as background task
        recorder.task = self.hass.async_create_task(
            self._run_and_cleanup(camera_name, recorder)
        )

        # Evaluate alarm (non-blocking — don't delay recording start)
        alarm_participation = cam_cfg.get(CONF_ALARM_PARTICIPATION, True)

        if should_notify(
            self.hass, alarm_participation, self.night_start, self.night_end
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

        if should_activate_lights(
            self.hass, alarm_participation, self.night_start, self.night_end
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
            all_sensors = cam_cfg.get(CONF_TRIGGER_SENSORS, [])
            any_still_on = False
            for sensor in all_sensors:
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
        except Exception:
            _LOGGER.exception(
                "Recording failed for %s — sending error notification",
                camera_name,
            )
            if self.notify_targets:
                await send_error_notification(
                    self.hass,
                    self.notify_targets,
                    camera_name,
                    f"Recording failed for camera {camera_name}",
                )
        finally:
            self.active_events.pop(camera_name, None)
            _LOGGER.debug("Cleaned up event for %s", camera_name)

    async def async_teardown(self) -> None:
        """Clean up on unload."""
        # Cancel all state listeners
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

        # Stop all active recordings
        for recorder in self.active_events.values():
            recorder.stop()
        self.active_events.clear()

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

        if camera and camera != "all":
            events = await self._coordinator.hass.async_add_executor_job(
                load_events_index,
                self._coordinator.media_path,
                camera,
            )
            events = events[offset : offset + limit]
        else:
            camera_names = list(self._coordinator.cameras.keys())
            events = await self._coordinator.hass.async_add_executor_job(
                load_all_events,
                self._coordinator.media_path,
                camera_names,
                limit,
                offset,
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

        # Build media URLs through our authenticated API endpoint
        base_media_url = f"/api/reolink_ha_sekurity/media/{camera_name}/{event_id}"

        segments_with_urls = []
        for seg in metadata.get("segments", []):
            segments_with_urls.append(
                {
                    **seg,
                    "url": f"{base_media_url}/{seg['file']}",
                }
            )

        snapshot_url = None
        if metadata.get("snapshot"):
            snapshot_url = f"{base_media_url}/{metadata['snapshot']}"

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
    requires_auth = True

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
