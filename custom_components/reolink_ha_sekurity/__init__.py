"""Reolink HA Sekurity — Core Integration."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import CONF_DASHBOARD_PATH, DOMAIN
from .coordinator import ReolinkHaSekurityCoordinator
from .storage import MediaPathUnavailable

_LOGGER = logging.getLogger(__name__)

# Cache buster for card resource URL
FRONTEND_SCRIPT_URL = f"/reolink_ha_sekurity/reolink-ha-sekurity-card.js?v={int(time.time())}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Reolink HA Sekurity — register frontend on load."""
    from homeassistant.components.http import StaticPathConfig

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
    # Migration: if dashboard_path is /lovelace/security, migrate it to new default
    if entry.data.get(CONF_DASHBOARD_PATH) == "/lovelace/security":
        new_data = dict(entry.data)
        new_data[CONF_DASHBOARD_PATH] = "/dashboard-security/security"
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.warning("[SEKURITY] Migrated dashboard_path config to /dashboard-security/security")

    coordinator = ReolinkHaSekurityCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    try:
        await coordinator.async_setup()
    except MediaPathUnavailable as exc:
        _LOGGER.warning("[SEKURITY] NAS media path not ready during setup: %s", exc)
        raise ConfigEntryNotReady(f"NAS media path unavailable: {exc}") from exc

    # Automatically register the Lovelace resource
    hass.async_create_task(_async_register_lovelace_resource(hass))

    return True


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Register the custom card in the Lovelace resource registry."""
    import asyncio
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState

    async def _add_resource(*args):
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

        url_base = "/reolink_ha_sekurity/reolink-ha-sekurity-card.js"
        exists = False

        for item in resources.async_items():
            if item.get("url", "").startswith(url_base):
                exists = True
                if item.get("url") != FRONTEND_SCRIPT_URL and hasattr(resources, "async_update_item"):
                    try:
                        await resources.async_update_item(
                            item["id"],
                            {"res_type": "module", "url": FRONTEND_SCRIPT_URL},
                        )
                        _LOGGER.info("Updated Reolink HA Sekurity Lovelace resource URL")
                    except Exception as e:
                        _LOGGER.error("Failed to update Lovelace resource: %s", e)
                break

        if not exists:
            try:
                await resources.async_create_item(
                    {"res_type": "module", "url": FRONTEND_SCRIPT_URL}
                )
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
