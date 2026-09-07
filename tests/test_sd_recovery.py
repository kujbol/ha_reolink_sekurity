"""Tests for Reolink MicroSD card segment recovery."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.reolink_ha_sekurity.sd_recovery import (
    get_reolink_host_api,
    download_segment_from_sd,
)


def test_get_reolink_host_api_none(mock_hass):
    api = get_reolink_host_api(mock_hass, "camera.unknown")
    assert api is None


@pytest.mark.asyncio
async def test_download_segment_from_sd(mock_hass, tmp_path):
    target = tmp_path / "seg001.mp4"
    start = datetime.now(timezone.utc)
    end = start

    with patch("custom_components.reolink_ha_sekurity.sd_recovery.get_reolink_host_api") as mock_get_api:
        mock_host = MagicMock()
        mock_host.get_vods = AsyncMock(return_value=["clip1.mp4"])
        mock_host.download_vod = AsyncMock(side_effect=lambda name, dest: Path(dest).write_bytes(b"X" * 2000))
        mock_get_api.return_value = mock_host

        ok = await download_segment_from_sd(mock_hass, "camera.front_door", target, start, end)
        assert ok is True
        assert target.exists()
        assert target.stat().st_size > 1024
