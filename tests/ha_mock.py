"""Mock homeassistant modules when running outside Home Assistant core environment."""

from __future__ import annotations

import sys
from typing import Callable
from types import ModuleType
from unittest.mock import MagicMock

def setup_ha_mocks():
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "__file__", None) is not None:
        return

    modules = [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.http",
        "homeassistant.components.http.auth",
        "homeassistant.components.switch",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.event",
        "homeassistant.helpers.typing",
        "homeassistant.helpers.restore_state",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.selector",
    ]

    for mod_name in modules:
        if mod_name not in sys.modules:
            mod = ModuleType(mod_name)
            sys.modules[mod_name] = mod

    # Core
    ha_core = sys.modules["homeassistant.core"]
    ha_core.HomeAssistant = MagicMock
    ha_core.Event = MagicMock
    ha_core.callback = lambda f: f
    ha_core.CoreState = MagicMock()
    ha_core.CALLBACK_TYPE = Callable[[], None]

    # Const
    ha_const = sys.modules["homeassistant.const"]
    ha_const.EVENT_STATE_CHANGED = "state_changed"
    ha_const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"
    ha_const.STATE_UNAVAILABLE = "unavailable"
    ha_const.STATE_UNKNOWN = "unknown"
    ha_const.STATE_ON = "on"
    ha_const.STATE_OFF = "off"
    ha_const.CONF_URL = "url"

    # Config Entries
    ha_config = sys.modules["homeassistant.config_entries"]
    ha_config.ConfigEntry = MagicMock
    
    class MockFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    ha_config.ConfigFlow = MockFlow
    ha_config.OptionsFlow = MockFlow

    # Exceptions
    ha_exc = sys.modules["homeassistant.exceptions"]
    class ConfigEntryNotReady(Exception): pass
    class HomeAssistantError(Exception): pass
    ha_exc.ConfigEntryNotReady = ConfigEntryNotReady
    ha_exc.HomeAssistantError = HomeAssistantError

    # Typing
    ha_typing = sys.modules["homeassistant.helpers.typing"]
    ha_typing.ConfigType = dict

    # Helpers
    ha_helpers = sys.modules["homeassistant.helpers"]
    ha_dr = sys.modules["homeassistant.helpers.device_registry"]
    ha_er = sys.modules["homeassistant.helpers.entity_registry"]
    ha_helpers.device_registry = ha_dr
    ha_helpers.entity_registry = ha_er

    ha_dr.async_get = MagicMock()
    ha_er.async_get = MagicMock()
    ha_er.async_entries_for_device = MagicMock(return_value=[])

    # HTTP
    ha_http = sys.modules["homeassistant.components.http"]
    class HomeAssistantView: pass
    class StaticPathConfig: pass
    ha_http.HomeAssistantView = HomeAssistantView
    ha_http.StaticPathConfig = StaticPathConfig

    # HTTP Auth
    ha_http_auth = sys.modules["homeassistant.components.http.auth"]
    ha_http_auth.async_sign_path = lambda hass, path, expiration: f"{path}?signed=true"

    # Switch
    ha_switch = sys.modules["homeassistant.components.switch"]
    class SwitchEntity: pass
    ha_switch.SwitchEntity = SwitchEntity

    # Restore state
    ha_restore = sys.modules["homeassistant.helpers.restore_state"]
    class RestoreEntity: pass
    ha_restore.RestoreEntity = RestoreEntity

    # Event helpers
    ha_event = sys.modules["homeassistant.helpers.event"]
    ha_event.async_track_state_change_event = MagicMock()
    ha_event.async_track_time_interval = MagicMock()
    ha_event.async_call_later = MagicMock()

    # Selectors
    ha_sel = sys.modules["homeassistant.helpers.selector"]
    ha_sel.EntitySelector = MagicMock
    ha_sel.EntitySelectorConfig = MagicMock
    ha_sel.TextSelector = MagicMock
    ha_sel.TextSelectorConfig = MagicMock
    ha_sel.NumberSelector = MagicMock
    ha_sel.NumberSelectorConfig = MagicMock
    ha_sel.NumberSelectorMode = MagicMock
    ha_sel.BooleanSelector = MagicMock
    ha_sel.TimeSelector = MagicMock

setup_ha_mocks()
