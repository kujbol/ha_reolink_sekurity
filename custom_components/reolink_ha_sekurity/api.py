"""REST API endpoints for Reolink HA Sekurity frontend card."""

from __future__ import annotations

import logging
import mimetypes
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path

if TYPE_CHECKING:
    from .coordinator import ReolinkHaSekurityCoordinator

from .concat import merge_event_segments
from .sd_recovery import download_segment_from_sd
from .metadata import (
    load_all_events,
    load_event_metadata,
    load_events_index,
)
from .storage import (
    get_event_dir,
    get_media_base_path,
)

_LOGGER = logging.getLogger(__name__)


class EventsAPIView(HomeAssistantView):
    """API endpoint to list events for the frontend card."""

    url = "/api/reolink_ha_sekurity/events"
    name = "api:reolink_ha_sekurity:events"
    requires_auth = True

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request):
        """Handle GET request — return events list."""
        try:
            params = request.query
            camera = params.get("camera", None)
            limit = int(params.get("limit", 25))
            offset = int(params.get("offset", 0))

            filter_type = params.get("filter", "security")

            if camera and camera != "all":
                camera_names = [camera]
            else:
                camera_names = list(self._coordinator.cameras.keys())

            events = await self._coordinator.hass.async_add_executor_job(
                load_all_events,
                self._coordinator.media_path,
                camera_names,
            )

            if filter_type == "security":
                events = [e for e in events if isinstance(e, dict) and e.get("event_type") != "motion"]

            # Sign snapshot URLs
            base_url = "/api/reolink_ha_sekurity/media"
            for ev in events:
                if isinstance(ev, dict) and ev.get("snapshot"):
                    raw_snap = f"{base_url}/{ev.get('camera')}/{ev.get('event_id')}/{ev['snapshot']}"
                    try:
                        ev["snapshot_url"] = async_sign_path(
                            self._coordinator.hass,
                            raw_snap,
                            timedelta(hours=1),
                        )
                    except Exception:
                        ev["snapshot_url"] = raw_snap
                elif isinstance(ev, dict):
                    ev["snapshot_url"] = None

            events = events[offset : offset + limit]

            active = {}
            for name, rec in getattr(self._coordinator, "active_events", {}).items():
                try:
                    event_type = rec.event_data.get("event_type") if hasattr(rec, "event_data") else ""
                    started_at = rec.started_at.isoformat() if hasattr(rec, "started_at") and rec.started_at else ""
                    active[name] = {
                        "event_id": getattr(rec, "event_id", ""),
                        "event_type": event_type,
                        "started_at": started_at,
                    }
                except Exception as e:
                    _LOGGER.warning("[SEKURITY] Error formatting active event info for %s: %s", name, e)

            return web.json_response(
                {
                    "events": events,
                    "active_events": active,
                    "cameras": list(self._coordinator.cameras.keys()),
                }
            )
        except Exception as exc:
            _LOGGER.exception("[SEKURITY] Error in EventsAPIView: %s", exc)
            return web.json_response({"error": f"Internal server error: {exc}"}, status=500)


class EventDetailAPIView(HomeAssistantView):
    """API endpoint to get event detail including segment file paths."""

    url = "/api/reolink_ha_sekurity/event/{event_id}"
    name = "api:reolink_ha_sekurity:event_detail"
    requires_auth = True

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request, event_id: str):
        """Handle GET request — return event metadata with media URLs."""
        try:
            camera_name = None
            parts = event_id.split("_")
            if len(parts) >= 3:
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

            base_media_url = f"/api/reolink_ha_sekurity/media/{camera_name}/{event_id}"
            event_dir = get_event_dir(self._coordinator.media_path, camera_name, event_id)
            camera_entity = metadata.get("camera_entity", "")

            # Filter out missing or 0-byte segment files on disk (attempting SD card recovery if needed)
            valid_segments = []
            for seg in metadata.get("segments", []) or []:
                if isinstance(seg, str):
                    seg = {"file": seg, "duration": 30, "index": 1}
                if not isinstance(seg, dict) or "file" not in seg:
                    continue

                seg_file = seg["file"]
                seg_path = event_dir / seg_file

                exists = False
                try:
                    exists = seg_path.exists() and seg_path.stat().st_size > 1024
                except Exception as exc:
                    _LOGGER.warning("[SEKURITY] Exception checking file %s: %s", seg_path, exc)

                if not exists:
                    if camera_entity:
                        start_iso = metadata.get("started_at")
                        try:
                            from datetime import datetime, timezone
                            start_dt = datetime.fromisoformat(start_iso) if start_iso else datetime.now(timezone.utc)
                        except Exception:
                            from datetime import datetime, timezone
                            start_dt = datetime.now(timezone.utc)
                        idx = seg.get("index", 1) - 1
                        dur = seg.get("duration", 30)
                        seg_start = start_dt + timedelta(seconds=idx * dur)
                        seg_end = seg_start + timedelta(seconds=dur)
                        try:
                            ok = await download_segment_from_sd(
                                self._coordinator.hass,
                                camera_entity,
                                seg_path,
                                seg_start,
                                seg_end,
                            )
                            if ok:
                                valid_segments.append(seg)
                        except Exception as exc:
                            _LOGGER.error("[SEKURITY] Error recovering segment from SD: %s", exc)
                else:
                    valid_segments.append(seg)

            segments_with_urls = []
            for seg in valid_segments:
                seg_file = seg.get("file")
                if not seg_file:
                    continue
                raw_url = f"{base_media_url}/{seg_file}"
                try:
                    signed = async_sign_path(
                        self._coordinator.hass,
                        raw_url,
                        timedelta(hours=1),
                    )
                except Exception:
                    signed = raw_url

                segments_with_urls.append(
                    {
                        **seg,
                        "url": signed,
                    }
                )

            snapshot_url = None
            if metadata.get("snapshot"):
                raw_snap = f"{base_media_url}/{metadata['snapshot']}"
                try:
                    snapshot_url = async_sign_path(
                        self._coordinator.hass,
                        raw_snap,
                        timedelta(hours=1),
                    )
                except Exception:
                    snapshot_url = raw_snap

            is_active = camera_name in getattr(self._coordinator, "active_events", {})

            # Check if single stream event.mp4 exists or can be merged
            stream_url = None
            if not is_active and metadata.get("segments"):
                try:
                    event_mp4_file = await self._coordinator.hass.async_add_executor_job(
                        merge_event_segments, event_dir, metadata
                    )
                    if event_mp4_file:
                        raw_stream = f"{base_media_url}/{event_mp4_file}"
                        try:
                            stream_url = async_sign_path(
                                self._coordinator.hass,
                                raw_stream,
                                timedelta(hours=1),
                            )
                        except Exception:
                            stream_url = raw_stream
                except Exception as exc:
                    _LOGGER.error("[SEKURITY] Failed merging segments for %s: %s", event_id, exc)

            return web.json_response(
                {
                    "metadata": metadata,
                    "stream_url": stream_url,
                    "segments": segments_with_urls,
                    "snapshot_url": snapshot_url,
                    "is_active": is_active,
                    "camera_entity": camera_entity,
                }
            )
        except Exception as exc:
            _LOGGER.exception("[SEKURITY] Error in EventDetailAPIView for %s: %s", event_id, exc)
            return web.json_response({"error": f"Internal server error: {exc}"}, status=500)


class MediaFileView(HomeAssistantView):
    """Serve media files (segments, snapshots) with HA authentication."""

    url = "/api/reolink_ha_sekurity/media/{camera_name}/{event_id}/{filename}"
    name = "api:reolink_ha_sekurity:media"
    requires_auth = True

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request, camera_name: str, event_id: str, filename: str):
        """Serve a media file."""
        try:
            for part in (camera_name, event_id, filename):
                if ".." in part or "/" in part or "\\" in part:
                    return web.Response(status=400, text="Invalid path")

            media_base = get_media_base_path(self._coordinator.media_path)
            file_path = media_base / camera_name / event_id / filename

            if not file_path.exists():
                return web.Response(status=404, text="File not found")

            content_type, _ = mimetypes.guess_type(str(file_path))
            if content_type is None:
                content_type = "application/octet-stream"

            return web.FileResponse(
                file_path,
                headers={"Content-Type": content_type},
            )
        except Exception as exc:
            _LOGGER.exception("[SEKURITY] Error serving media file %s/%s/%s: %s", camera_name, event_id, filename, exc)
            return web.Response(status=500, text="Internal server error")


class ConfigAPIView(HomeAssistantView):
    """API endpoint to get coordinator config (for debugging)."""

    url = "/api/reolink_ha_sekurity/config"
    name = "api:reolink_ha_sekurity:config"
    requires_auth = True

    def __init__(self, coordinator: ReolinkHaSekurityCoordinator):
        self._coordinator = coordinator

    async def get(self, request):
        try:
            config_dir = Path(self._coordinator.hass.config.config_dir)
            go2rtc_path = config_dir / "go2rtc.yaml"
            go2rtc_content = None
            if go2rtc_path.exists():
                try:
                    go2rtc_content = go2rtc_path.read_text()
                except Exception as e:
                    go2rtc_content = f"Error reading: {e}"

            return web.json_response({
                "dashboard_path": self._coordinator.dashboard_path,
                "notify_targets": self._coordinator.notify_targets,
                "error_notify_targets": self._coordinator.error_notify_targets,
                "media_path": self._coordinator.media_path,
                "cameras": list(self._coordinator.cameras.keys()),
                "config_raw": dict(self._coordinator.config),
                "go2rtc_exists": go2rtc_path.exists(),
                "go2rtc_content": go2rtc_content,
            })
        except Exception as exc:
            _LOGGER.exception("[SEKURITY] Error in ConfigAPIView: %s", exc)
            return web.json_response({"error": f"Internal server error: {exc}"}, status=500)
