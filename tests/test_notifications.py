"""Tests for push notification payloads."""

from unittest.mock import AsyncMock, patch
import pytest

from custom_components.reolink_ha_sekurity.notifications import (
    send_event_notification,
    send_error_notification,
)


@pytest.mark.asyncio
async def test_send_event_notification(mock_hass):
    event_data = {
        "event_id": "20260908_120000_front_door",
        "camera": "front_door",
        "event_type": "person",
        "snapshot": "snapshot.jpg",
    }
    targets = ["notify.mobile_app_phone1"]

    with patch("custom_components.reolink_ha_sekurity.notifications._send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        await send_event_notification(mock_hass, event_data, targets, "/dashboard-security/security")

        assert mock_send.called
        call_args = mock_send.call_args[0]
        service_name = call_args[1]
        payload = call_args[2]

        assert service_name == "mobile_app_phone1"
        assert "Person" in payload["title"]
        assert payload["data"]["clickAction"] == "/dashboard-security/security?event_id=20260908_120000_front_door"


@pytest.mark.asyncio
async def test_send_error_notification(mock_hass):
    targets = ["notify.mobile_app_admin"]

    with patch("custom_components.reolink_ha_sekurity.notifications._send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        await send_error_notification(mock_hass, targets, "front_door", "NAS Storage Unavailable")

        assert mock_send.called
        call_args = mock_send.call_args[0]
        service_name = call_args[1]
        payload = call_args[2]

        assert service_name == "mobile_app_admin"
        assert "Recording Error" in payload["title"]
        assert "NAS Storage Unavailable" in payload["message"]
