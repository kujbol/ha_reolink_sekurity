"""On-camera MicroSD card recovery engine for Reolink HA Sekurity.

If network jitter or Wi-Fi drops cause a segment recording to fail or turn up
0-bytes on NAS, this module queries the official Home Assistant Reolink integration
API (reolink_aio) to download the missing clip directly from the camera's MicroSD card.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

_LOGGER = logging.getLogger(__name__)


def get_reolink_host_api(hass: HomeAssistant, camera_entity_id: str) -> Any | None:
    """Locate the reolink_aio Host API object from Home Assistant data."""
    try:
        ent_reg = er.async_get(hass)
        camera_entry = ent_reg.async_get(camera_entity_id)
        if not camera_entry:
            return None

        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(camera_entry.device_id) if camera_entry.device_id else None

        config_entry_ids = set()
        if camera_entry.config_entry_id:
            config_entry_ids.add(camera_entry.config_entry_id)
        if device and hasattr(device, "config_entries"):
            config_entry_ids.update(device.config_entries)

        reolink_data = hass.data.get("reolink", {})
        for entry_id in config_entry_ids:
            if entry_id in reolink_data:
                entry_data = reolink_data[entry_id]
                if hasattr(entry_data, "api"):
                    return entry_data.api
                if hasattr(entry_data, "host"):
                    return entry_data.host
                if isinstance(entry_data, dict):
                    return entry_data.get("api") or entry_data.get("host")
    except Exception as exc:
        _LOGGER.debug("[SEKURITY] Error finding Reolink host API for %s: %s", camera_entity_id, exc)

    return None


async def download_segment_from_sd(
    hass: HomeAssistant,
    camera_entity_id: str,
    target_path: Path,
    start_time: datetime,
    end_time: datetime,
) -> bool:
    """Attempt to download a missing segment directly from the camera's MicroSD card.

    Returns True if successfully downloaded and target_path is non-empty.
    """
    host = get_reolink_host_api(hass, camera_entity_id)
    if host is None:
        _LOGGER.debug(
            "[SEKURITY] Reolink Host API not available for %s — skipping SD recovery",
            camera_entity_id,
        )
        return False

    try:
        # Convert times to naive local time as expected by reolink_aio
        search_start = start_time - timedelta(seconds=15)
        search_end = end_time + timedelta(seconds=15)

        _LOGGER.info(
            "[SEKURITY] Searching camera SD card for %s between %s and %s",
            camera_entity_id, search_start.isoformat(), search_end.isoformat(),
        )

        vods = None
        if hasattr(host, "get_vods"):
            vods = await host.get_vods(search_start, search_end)
        elif hasattr(host, "search_vod"):
            vods = await host.search_vod(search_start, search_end)

        if not vods:
            _LOGGER.debug(
                "[SEKURITY] No VOD files found on SD card for %s in window",
                camera_entity_id,
            )
            return False

        # Pick best matching VOD file
        target_vod = vods[0]
        file_name = getattr(target_vod, "file", None) or getattr(target_vod, "name", None) or str(target_vod)

        _LOGGER.info(
            "[SEKURITY] Found SD card VOD clip '%s' for %s — downloading to %s",
            file_name, camera_entity_id, target_path.name,
        )

        if hasattr(host, "download_vod"):
            await host.download_vod(file_name, str(target_path))
        elif hasattr(host, "download"):
            await host.download(file_name, str(target_path))
        else:
            return False

        if target_path.exists() and target_path.stat().st_size > 1024:
            _LOGGER.info(
                "[SEKURITY] ✅ Successfully recovered segment %s (%d KB) from camera SD card!",
                target_path.name, target_path.stat().st_size // 1024,
            )
            return True

    except Exception as exc:
        _LOGGER.warning(
            "[SEKURITY] SD card recovery failed for %s (%s): %s",
            camera_entity_id, target_path.name, exc,
        )

    return False
