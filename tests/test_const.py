"""Tests for Reolink HA Sekurity constants."""

from custom_components.reolink_ha_sekurity.const import (
    DOMAIN,
    CONF_MEDIA_PATH,
    CONF_NOTIFY_TARGETS,
    CONF_ERROR_NOTIFY_TARGETS,
    EVENT_TYPE_PRIORITY,
    DEFAULT_CLIP_DURATION,
    DEFAULT_LOOKBACK,
)


def test_domain():
    assert DOMAIN == "reolink_ha_sekurity"


def test_config_keys():
    assert CONF_MEDIA_PATH == "media_path"
    assert CONF_NOTIFY_TARGETS == "notify_targets"
    assert CONF_ERROR_NOTIFY_TARGETS == "error_notify_targets"


def test_event_type_priority():
    assert EVENT_TYPE_PRIORITY["person"] > EVENT_TYPE_PRIORITY["vehicle"]
    assert EVENT_TYPE_PRIORITY["vehicle"] > EVENT_TYPE_PRIORITY["motion"]
    assert EVENT_TYPE_PRIORITY["person"] > EVENT_TYPE_PRIORITY["motion"]


def test_defaults():
    assert DEFAULT_CLIP_DURATION == 30
    assert DEFAULT_LOOKBACK == 5
