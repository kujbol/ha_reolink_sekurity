"""Unit tests for REST API endpoints in api.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.reolink_ha_sekurity.api import (
    ConfigAPIView,
    EventDetailAPIView,
    EventsAPIView,
    MediaFileView,
)
from custom_components.reolink_ha_sekurity.coordinator import ReolinkHaSekurityCoordinator


@pytest.fixture
def mock_coordinator(mock_hass, mock_config_entry, tmp_media_dir):
    mock_config_entry.data["media_path"] = str(tmp_media_dir)
    coord = ReolinkHaSekurityCoordinator(mock_hass, mock_config_entry)
    return coord


@pytest.mark.asyncio
async def test_events_api_view(mock_coordinator):
    view = EventsAPIView(mock_coordinator)
    request = MagicMock()
    request.query = {"camera": "all", "limit": "10", "offset": "0", "filter": "security"}

    with patch("custom_components.reolink_ha_sekurity.api.load_all_events", return_value=[]):
        resp = await view.get(request)
        assert resp.status == 200


@pytest.mark.asyncio
async def test_event_detail_api_view_invalid_id(mock_coordinator):
    view = EventDetailAPIView(mock_coordinator)
    request = MagicMock()
    resp = await view.get(request, event_id="invalid")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_media_file_view_path_traversal(mock_coordinator):
    view = MediaFileView(mock_coordinator)
    request = MagicMock()
    resp = await view.get(request, camera_name="front_door", event_id="../etc", filename="passwd")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_config_api_view(mock_coordinator):
    view = ConfigAPIView(mock_coordinator)
    request = MagicMock()
    resp = await view.get(request)
    assert resp.status == 200
