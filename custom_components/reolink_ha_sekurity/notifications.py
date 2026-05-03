"""Cross-platform push notifications for Reolink HA Sekurity."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


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
    deep_link = f"{dashboard_path}?event_id={event_id}"

    for target in notify_targets:
        service_name = target.replace("notify.", "")
        try:
            await hass.services.async_call(
                "notify",
                service_name,
                {
                    "title": f"\U0001f6a8 {event_type.title()} — {camera}",
                    "message": "Tap to view",
                    "data": {
                        # iOS — critical notification (bypasses DND)
                        "push": {
                            "sound": {
                                "name": "default",
                                "critical": 1,
                                "volume": 1.0,
                            }
                        },
                        # Android — high priority
                        "priority": "high",
                        "ttl": 0,
                        "channel": "alarm",
                        # Deep-link for both platforms
                        "url": deep_link,
                        "clickAction": deep_link,
                        # Grouping & deduplication
                        "tag": event_id,
                        "group": "reolink_ha_sekurity",
                    },
                },
                blocking=False,
            )
            _LOGGER.info(
                "Sent notification to %s for event %s", target, event_id
            )
        except Exception:
            _LOGGER.exception("Failed to send notification to %s", target)


async def send_error_notification(
    hass: HomeAssistant,
    notify_targets: list[str],
    camera_name: str,
    error_msg: str,
) -> None:
    """Send a low-priority error notification about recording failure."""
    for target in notify_targets:
        service_name = target.replace("notify.", "")
        try:
            await hass.services.async_call(
                "notify",
                service_name,
                {
                    "title": f"\u26a0\ufe0f Recording failed — {camera_name}",
                    "message": error_msg,
                    "data": {
                        # Low priority — don't wake the user at night for this
                        "priority": "low",
                        "ttl": 3600,
                        "channel": "recording_error",
                        "tag": f"error_{camera_name}",
                        "group": "reolink_ha_sekurity_errors",
                    },
                },
                blocking=False,
            )
        except Exception:
            _LOGGER.exception("Failed to send error notification to %s", target)
