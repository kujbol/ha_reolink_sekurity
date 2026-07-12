"""Cross-platform push notifications for Reolink HA Sekurity.

Supports both iOS and Android HA Companion App devices.
Each platform ignores data keys it doesn't understand, so we include
all platform-specific keys in a single payload.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _resolve_service(target: str) -> str:
    """Normalise a notify target to a service name.

    Handles both 'notify.mobile_app_pixel' and 'mobile_app_pixel' formats.
    """
    return target.removeprefix("notify.")


def _has_notify_service(hass: HomeAssistant, service_name: str) -> bool:
    """Check whether a notify service is registered."""
    return hass.services.has_service("notify", service_name)


async def _send(
    hass: HomeAssistant,
    service_name: str,
    payload: dict,
) -> bool:
    """Send a notification via a notify service. Returns True on success."""
    if not _has_notify_service(hass, service_name):
        _LOGGER.warning(
            "[SEKURITY] Notify service 'notify.%s' does not exist — "
            "check your notification targets in the integration config",
            service_name,
        )
        return False

    try:
        await hass.services.async_call(
            "notify",
            service_name,
            payload,
            blocking=True,
        )
        return True
    except Exception:
        _LOGGER.exception(
            "[SEKURITY] Failed to call notify.%s", service_name
        )
        return False


async def send_event_notification(
    hass: HomeAssistant,
    event_data: dict,
    notify_targets: list[str],
    dashboard_path: str,
) -> None:
    """Send high-priority push notification to all configured devices.

    Works on both iOS (critical notification) and Android (high priority).
    Tap deep-links to the event in the Lovelace card.
    """
    event_id = event_data["event_id"]
    camera = event_data["camera"]
    event_type = event_data["event_type"]
    snapshot = event_data.get("snapshot")

    # Use homeassistant://navigate/ for reliable deep linking on both platforms.
    # Strip leading slash from dashboard_path to avoid double-slash in the URI.
    nav_path = dashboard_path.lstrip("/")
    deep_link = f"homeassistant://navigate/{nav_path}?event_id={event_id}"

    title = f"\U0001f6a8 {event_type.title()} — {camera}"
    message = "Tap to view"

    data: dict = {
        # --- iOS (Companion App) ---
        "push": {
            "sound": {
                "name": "default",
                "critical": 1,
                "volume": 1.0,
            },
            "interruption-level": "critical",
        },
        "url": deep_link,                  # iOS deep-link on tap
        "action_data": {"event_id": event_id},

        # --- Android (Companion App) ---
        "importance": "high",              # Android notification importance
        "priority": "high",               # FCM priority
        "ttl": 0,                         # Deliver immediately
        "channel": "alarm",               # Android notification channel
        "clickAction": deep_link,          # Android deep-link on tap
        "color": "#FF0000",               # Android accent colour
        "sticky": True,                   # Persist until dismissed

        # --- Both platforms ---
        "tag": event_id,                  # Replace previous notification for same event
        "group": "reolink_ha_sekurity",   # Group notifications together
    }

    # Attach snapshot image if available (works on both platforms)
    if snapshot:
        image_url = f"/api/reolink_ha_sekurity/media/{camera}/{event_id}/{snapshot}"
        data["image"] = image_url          # Android
        data["attachment"] = {             # iOS
            "url": image_url,
            "content-type": "jpeg",
        }

    payload = {"title": title, "message": message, "data": data}

    for target in notify_targets:
        service_name = _resolve_service(target)
        ok = await _send(hass, service_name, payload)
        if ok:
            _LOGGER.info(
                "Sent event notification to %s for %s", target, event_id
            )


async def send_error_notification(
    hass: HomeAssistant,
    notify_targets: list[str],
    camera_name: str,
    error_msg: str,
) -> None:
    """Send an error notification about recording/NAS failure.

    Sent once per outage (dedup handled by caller).
    """
    title = f"\u26a0\ufe0f Recording Error — {camera_name}"

    data: dict = {
        # --- iOS ---
        "push": {
            "sound": {"name": "default"},
            "interruption-level": "active",
        },

        # --- Android ---
        "importance": "high",
        "priority": "high",
        "ttl": 0,
        "channel": "recording_error",
        "color": "#FFA500",
        "sticky": True,

        # --- Both ---
        "tag": f"error_{camera_name}",
        "group": "reolink_ha_sekurity_errors",
    }

    payload = {"title": title, "message": error_msg, "data": data}

    for target in notify_targets:
        service_name = _resolve_service(target)
        ok = await _send(hass, service_name, payload)
        if ok:
            _LOGGER.info(
                "Sent error notification to %s for %s", target, camera_name
            )
