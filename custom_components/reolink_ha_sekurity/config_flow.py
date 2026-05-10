"""Config flow for Reolink HA Sekurity."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    BooleanSelector,
    TimeSelector,
)

from .const import (
    CONF_CAMERA_ENTITY,
    CONF_CAMERA_NAME,
    CONF_CAMERAS,
    CONF_CLIP_DURATION,
    CONF_DASHBOARD_PATH,
    CONF_LIGHT_ENTITIES,
    CONF_LIGHT_TIMEOUT,
    CONF_LOOKBACK,
    CONF_MEDIA_PATH,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_NOTIFY_TARGETS,
    CONF_POST_ROLL,
    CONF_TRIGGER_SENSORS,
    CONF_RECORD_SENSORS,
    CONF_ALARM_SENSORS,
    DEFAULT_CLIP_DURATION,
    DEFAULT_LIGHT_TIMEOUT,
    DEFAULT_LOOKBACK,
    DEFAULT_MEDIA_PATH,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DEFAULT_POST_ROLL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _derive_camera_name(entity_id: str) -> str:
    """Derive a friendly name from a camera entity ID.

    camera.front_door_fluent -> front_door
    camera.front_door_clear -> front_door
    camera.front_door -> front_door
    """
    name = entity_id.replace("camera.", "")
    # Strip common Reolink stream suffixes
    for suffix in ("_fluent", "_clear", "_balanced", "_snapshots_fluent", "_snapshots_clear"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _find_device_sensors(
    hass, camera_entity_id: str
) -> list[dict[str, str]]:
    """Find all binary sensors on the same device as the camera.

    Auto-discovers person, vehicle, motion, pet, animal, visitor sensors.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    camera_entry = ent_reg.async_get(camera_entity_id)
    if camera_entry is None or camera_entry.device_id is None:
        return []

    device_id = camera_entry.device_id
    sensors = []

    detection_types = {
        "person": "Person",
        "vehicle": "Vehicle",
        "motion": "Motion",
        "pet": "Pet",
        "animal": "Animal",
        "visitor": "Visitor (Doorbell)",
    }

    for entity in er.async_entries_for_device(ent_reg, device_id):
        if entity.domain != "binary_sensor":
            continue

        check_strings = [entity.entity_id.lower()]
        if entity.unique_id:
            check_strings.append(entity.unique_id.lower())
        if entity.original_name:
            check_strings.append(entity.original_name.lower())

        found_type = None
        found_label = None

        for check_str in check_strings:
            for det_type, label in detection_types.items():
                if f"_{det_type}" in check_str or f" {det_type}" in check_str or f"-{det_type}" in check_str:
                    found_type = det_type
                    found_label = label
                    break
            if found_type:
                break
        
        if not found_type:
            found_type = "unknown"
            found_label = "Unknown Sensor"

        sensors.append(
            {
                "entity_id": entity.entity_id,
                "type": found_type,
                "label": found_label,
            }
        )

    return sensors


class ReolinkHaSekurityConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Reolink HA Sekurity."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._global_data: dict[str, Any] = {}
        self._cameras: dict[str, dict] = {}
        self._available_sensors: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: Global settings."""
        errors = {}

        if user_input is not None:
            self._global_data = {
                CONF_MEDIA_PATH: user_input.get(CONF_MEDIA_PATH, DEFAULT_MEDIA_PATH),
                CONF_NOTIFY_TARGETS: [
                    t.strip()
                    for t in user_input.get(CONF_NOTIFY_TARGETS, "").split(",")
                    if t.strip()
                ],
                CONF_NIGHT_START: user_input.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
                CONF_NIGHT_END: user_input.get(CONF_NIGHT_END, DEFAULT_NIGHT_END),
                CONF_LIGHT_ENTITIES: user_input.get(CONF_LIGHT_ENTITIES, []),
                CONF_LIGHT_TIMEOUT: user_input.get(
                    CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT
                ),
                CONF_DASHBOARD_PATH: user_input.get(
                    CONF_DASHBOARD_PATH, "/lovelace/security"
                ),
            }
            return await self.async_step_camera()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MEDIA_PATH, default=DEFAULT_MEDIA_PATH
                    ): TextSelector(TextSelectorConfig(type="text")),
                    vol.Required(
                        CONF_NOTIFY_TARGETS,
                        default="notify.mobile_app_",
                    ): TextSelector(
                        TextSelectorConfig(
                            type="text",
                            multiline=False,
                        )
                    ),
                    vol.Required(
                        CONF_NIGHT_START, default=DEFAULT_NIGHT_START
                    ): TimeSelector(),
                    vol.Required(
                        CONF_NIGHT_END, default=DEFAULT_NIGHT_END
                    ): TimeSelector(),
                    vol.Optional(CONF_LIGHT_ENTITIES, default=[]): EntitySelector(
                        EntitySelectorConfig(domain="light", multiple=True)
                    ),
                    vol.Required(
                        CONF_LIGHT_TIMEOUT, default=DEFAULT_LIGHT_TIMEOUT
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=60, max=1800, step=60, mode=NumberSelectorMode.BOX, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_DASHBOARD_PATH, default="/lovelace/security"
                    ): TextSelector(TextSelectorConfig(type="text")),
                }
            ),
            errors=errors,
            description_placeholders={
                "notify_hint": "Comma-separated, e.g. notify.mobile_app_phone1,notify.mobile_app_phone2"
            },
        )

    async def async_step_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2a: Pick a camera entity."""
        if user_input is not None:
            self._current_camera_entity = user_input[CONF_CAMERA_ENTITY]
            self._current_camera_name = user_input.get(
                CONF_CAMERA_NAME, _derive_camera_name(self._current_camera_entity)
            )
            self._current_camera_name = re.sub(
                r"[^a-zA-Z0-9_]", "_", self._current_camera_name
            ).lower()

            # Auto-discover sensors on this camera's device
            self._available_sensors = _find_device_sensors(
                self.hass, self._current_camera_entity
            )
            return await self.async_step_camera_sensors()

        return self.async_show_form(
            step_id="camera",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CAMERA_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="camera")
                    ),
                    vol.Optional(CONF_CAMERA_NAME): TextSelector(
                        TextSelectorConfig(type="text")
                    ),
                }
            ),
        )

    async def async_step_camera_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2b: Select sensors and recording settings for the camera."""
        errors = {}

        if user_input is not None:
            record_sensors = user_input.get(CONF_RECORD_SENSORS, [])
            alarm_sensors = user_input.get(CONF_ALARM_SENSORS, [])

            if not record_sensors:
                errors["base"] = "no_sensors"
            else:
                camera_name = self._current_camera_name
                self._cameras[camera_name] = {
                    CONF_CAMERA_ENTITY: self._current_camera_entity,
                    CONF_CAMERA_NAME: camera_name,
                    CONF_RECORD_SENSORS: record_sensors,
                    CONF_ALARM_SENSORS: alarm_sensors,
                    CONF_CLIP_DURATION: user_input.get(
                        CONF_CLIP_DURATION, DEFAULT_CLIP_DURATION
                    ),
                    CONF_LOOKBACK: user_input.get(CONF_LOOKBACK, DEFAULT_LOOKBACK),
                    CONF_POST_ROLL: user_input.get(CONF_POST_ROLL, DEFAULT_POST_ROLL),
                    CONF_ALARM_PARTICIPATION: user_input.get(
                        CONF_ALARM_PARTICIPATION, True
                    ),
                }

                # Check if user wants to add more cameras
                if user_input.get("add_another", False):
                    return await self.async_step_camera()

                # Done — create the entry
                return self.async_create_entry(
                    title="Reolink HA Sekurity",
                    data={
                        **self._global_data,
                        CONF_CAMERAS: self._cameras,
                    },
                )

        # Pre-select person and vehicle sensors from discovered sensors
        default_sensors = [
            s["entity_id"]
            for s in self._available_sensors
            if s["type"] in ("person", "vehicle")
        ]

        # If we found sensors on the device, show only those
        if self._available_sensors:
            sensor_entity_ids = [s["entity_id"] for s in self._available_sensors]
            sensor_schema = vol.Required(
                CONF_RECORD_SENSORS, default=default_sensors
            )
            alarm_sensor_schema = vol.Required(
                CONF_ALARM_SENSORS, default=default_sensors
            )
            sensor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="binary_sensor",
                    multiple=True,
                    include_entities=sensor_entity_ids,
                )
            )
        else:
            # Fallback — no auto-discovery, show all binary sensors
            sensor_schema = vol.Required(CONF_RECORD_SENSORS, default=[])
            alarm_sensor_schema = vol.Required(CONF_ALARM_SENSORS, default=[])
            sensor_selector = EntitySelector(
                EntitySelectorConfig(domain="binary_sensor", multiple=True)
            )

        return self.async_show_form(
            step_id="camera_sensors",
            data_schema=vol.Schema(
                {
                    sensor_schema: sensor_selector,
                    alarm_sensor_schema: sensor_selector,
                    vol.Required(
                        CONF_CLIP_DURATION, default=DEFAULT_CLIP_DURATION
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10, max=120, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_LOOKBACK, default=DEFAULT_LOOKBACK
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=10, step=1, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_POST_ROLL, default=DEFAULT_POST_ROLL
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=60, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_ALARM_PARTICIPATION, default=True
                    ): BooleanSelector(),
                    vol.Optional("add_another", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "camera_name": self._current_camera_name,
                "sensor_count": str(len(self._available_sensors)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ReolinkHaSekurityOptionsFlow:
        """Get the options flow handler."""
        return ReolinkHaSekurityOptionsFlow(config_entry)


class ReolinkHaSekurityOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Reolink HA Sekurity."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Main options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["global_settings", "add_camera", "edit_camera", "remove_camera"],
        )

    async def async_step_global_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit global settings."""
        current = self._config_entry.data

        if user_input is not None:
            new_data = {**current}
            new_data[CONF_MEDIA_PATH] = user_input.get(
                CONF_MEDIA_PATH, current.get(CONF_MEDIA_PATH, DEFAULT_MEDIA_PATH)
            )
            new_data[CONF_NOTIFY_TARGETS] = [
                t.strip()
                for t in user_input.get(CONF_NOTIFY_TARGETS, "").split(",")
                if t.strip()
            ]
            new_data[CONF_NIGHT_START] = user_input.get(
                CONF_NIGHT_START, current.get(CONF_NIGHT_START, DEFAULT_NIGHT_START)
            )
            new_data[CONF_NIGHT_END] = user_input.get(
                CONF_NIGHT_END, current.get(CONF_NIGHT_END, DEFAULT_NIGHT_END)
            )
            new_data[CONF_LIGHT_ENTITIES] = user_input.get(
                CONF_LIGHT_ENTITIES, current.get(CONF_LIGHT_ENTITIES, [])
            )
            new_data[CONF_LIGHT_TIMEOUT] = user_input.get(
                CONF_LIGHT_TIMEOUT, current.get(CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT)
            )
            new_data[CONF_DASHBOARD_PATH] = user_input.get(
                CONF_DASHBOARD_PATH,
                current.get(CONF_DASHBOARD_PATH, "/lovelace/security"),
            )
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        notify_str = ",".join(
            current.get(CONF_NOTIFY_TARGETS, [])
        )

        return self.async_show_form(
            step_id="global_settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MEDIA_PATH,
                        default=current.get(CONF_MEDIA_PATH, DEFAULT_MEDIA_PATH),
                    ): TextSelector(TextSelectorConfig(type="text")),
                    vol.Required(
                        CONF_NOTIFY_TARGETS,
                        default=notify_str,
                    ): TextSelector(TextSelectorConfig(type="text")),
                    vol.Required(
                        CONF_NIGHT_START,
                        default=current.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
                    ): TimeSelector(),
                    vol.Required(
                        CONF_NIGHT_END,
                        default=current.get(CONF_NIGHT_END, DEFAULT_NIGHT_END),
                    ): TimeSelector(),
                    vol.Optional(
                        CONF_LIGHT_ENTITIES,
                        default=current.get(CONF_LIGHT_ENTITIES, []),
                    ): EntitySelector(
                        EntitySelectorConfig(domain="light", multiple=True)
                    ),
                    vol.Required(
                        CONF_LIGHT_TIMEOUT,
                        default=current.get(CONF_LIGHT_TIMEOUT, DEFAULT_LIGHT_TIMEOUT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=60, max=1800, step=60, mode=NumberSelectorMode.BOX, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_DASHBOARD_PATH,
                        default=current.get(CONF_DASHBOARD_PATH, "/lovelace/security"),
                    ): TextSelector(TextSelectorConfig(type="text")),
                }
            ),
        )

    async def async_step_add_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add a new camera."""
        if user_input is not None:
            self._current_camera_entity = user_input[CONF_CAMERA_ENTITY]
            self._current_camera_name = user_input.get(
                CONF_CAMERA_NAME, _derive_camera_name(self._current_camera_entity)
            )
            self._current_camera_name = re.sub(
                r"[^a-zA-Z0-9_]", "_", self._current_camera_name
            ).lower()

            # Auto-discover sensors on this camera's device
            self._available_sensors = _find_device_sensors(
                self.hass, self._current_camera_entity
            )
            return await self.async_step_add_camera_sensors()

        return self.async_show_form(
            step_id="add_camera",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CAMERA_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="camera")
                    ),
                    vol.Optional(CONF_CAMERA_NAME): TextSelector(
                        TextSelectorConfig(type="text")
                    ),
                }
            ),
        )

    async def async_step_add_camera_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2b: Select sensors for new camera."""
        errors = {}
        current = self._config_entry.data

        if user_input is not None:
            record_sensors = user_input.get(CONF_RECORD_SENSORS, [])
            alarm_sensors = user_input.get(CONF_ALARM_SENSORS, [])

            if not record_sensors:
                errors["base"] = "no_sensors"
            else:
                new_data = {**current}
                cameras = dict(new_data.get(CONF_CAMERAS, {}))
                cameras[self._current_camera_name] = {
                    CONF_CAMERA_ENTITY: self._current_camera_entity,
                    CONF_CAMERA_NAME: self._current_camera_name,
                    CONF_RECORD_SENSORS: record_sensors,
                    CONF_ALARM_SENSORS: alarm_sensors,
                    CONF_CLIP_DURATION: user_input.get(
                        CONF_CLIP_DURATION, DEFAULT_CLIP_DURATION
                    ),
                    CONF_LOOKBACK: user_input.get(CONF_LOOKBACK, DEFAULT_LOOKBACK),
                    CONF_POST_ROLL: user_input.get(CONF_POST_ROLL, DEFAULT_POST_ROLL),
                }
                new_data[CONF_CAMERAS] = cameras
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

        default_sensors = [
            s["entity_id"]
            for s in self._available_sensors
            if s["type"] in ("person", "vehicle")
        ]

        if self._available_sensors:
            sensor_entity_ids = [s["entity_id"] for s in self._available_sensors]
            sensor_schema = vol.Required(
                CONF_RECORD_SENSORS, default=default_sensors
            )
            alarm_sensor_schema = vol.Required(
                CONF_ALARM_SENSORS, default=default_sensors
            )
            sensor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="binary_sensor",
                    multiple=True,
                    include_entities=sensor_entity_ids,
                )
            )
        else:
            sensor_schema = vol.Required(CONF_RECORD_SENSORS, default=[])
            alarm_sensor_schema = vol.Required(CONF_ALARM_SENSORS, default=[])
            sensor_selector = EntitySelector(
                EntitySelectorConfig(domain="binary_sensor", multiple=True)
            )

        return self.async_show_form(
            step_id="add_camera_sensors",
            data_schema=vol.Schema(
                {
                    sensor_schema: sensor_selector,
                    alarm_sensor_schema: sensor_selector,
                    vol.Required(
                        CONF_CLIP_DURATION, default=DEFAULT_CLIP_DURATION
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10, max=120, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_LOOKBACK, default=DEFAULT_LOOKBACK
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=10, step=1, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_POST_ROLL, default=DEFAULT_POST_ROLL
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=60, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "camera_name": self._current_camera_name,
                "sensor_count": str(len(self._available_sensors)),
            },
        )

    async def async_step_edit_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit an existing camera."""
        current = self._config_entry.data
        cameras = current.get(CONF_CAMERAS, {})

        if not cameras:
            return self.async_abort(reason="no_cameras")

        if user_input is not None:
            self._current_camera_name = user_input["camera_name"]
            self._current_camera_cfg = cameras[self._current_camera_name]
            
            # Auto-discover sensors on this camera's device
            self._available_sensors = _find_device_sensors(
                self.hass, self._current_camera_cfg.get(CONF_CAMERA_ENTITY)
            )
            return await self.async_step_edit_camera_sensors()

        camera_names = {name: name for name in cameras}

        return self.async_show_form(
            step_id="edit_camera",
            data_schema=vol.Schema(
                {
                    vol.Required("camera_name"): vol.In(camera_names),
                }
            ),
        )

    async def async_step_edit_camera_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit sensors for an existing camera."""
        errors = {}
        current = self._config_entry.data

        if user_input is not None:
            record_sensors = user_input.get(CONF_RECORD_SENSORS, [])
            alarm_sensors = user_input.get(CONF_ALARM_SENSORS, [])

            if not record_sensors:
                errors["base"] = "no_sensors"
            else:
                new_data = {**current}
                cameras = dict(new_data.get(CONF_CAMERAS, {}))
                cfg = dict(cameras[self._current_camera_name])
                
                cfg[CONF_RECORD_SENSORS] = record_sensors
                cfg[CONF_ALARM_SENSORS] = alarm_sensors
                cfg[CONF_CLIP_DURATION] = user_input.get(CONF_CLIP_DURATION, DEFAULT_CLIP_DURATION)
                cfg[CONF_LOOKBACK] = user_input.get(CONF_LOOKBACK, DEFAULT_LOOKBACK)
                cfg[CONF_POST_ROLL] = user_input.get(CONF_POST_ROLL, DEFAULT_POST_ROLL)
                
                cameras[self._current_camera_name] = cfg
                new_data[CONF_CAMERAS] = cameras
                
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

        cfg = self._current_camera_cfg
        # Migration from trigger_sensors if it hasn't been migrated yet
        def_record = cfg.get(CONF_RECORD_SENSORS, cfg.get(CONF_TRIGGER_SENSORS, []))
        def_alarm = cfg.get(CONF_ALARM_SENSORS, cfg.get(CONF_TRIGGER_SENSORS, []))

        if self._available_sensors:
            sensor_entity_ids = [s["entity_id"] for s in self._available_sensors]
            sensor_schema = vol.Required(CONF_RECORD_SENSORS, default=def_record)
            alarm_sensor_schema = vol.Required(CONF_ALARM_SENSORS, default=def_alarm)
            sensor_selector = EntitySelector(
                EntitySelectorConfig(
                    domain="binary_sensor",
                    multiple=True,
                    include_entities=sensor_entity_ids,
                )
            )
        else:
            sensor_schema = vol.Required(CONF_RECORD_SENSORS, default=def_record)
            alarm_sensor_schema = vol.Required(CONF_ALARM_SENSORS, default=def_alarm)
            sensor_selector = EntitySelector(
                EntitySelectorConfig(domain="binary_sensor", multiple=True)
            )

        return self.async_show_form(
            step_id="edit_camera_sensors",
            data_schema=vol.Schema(
                {
                    sensor_schema: sensor_selector,
                    alarm_sensor_schema: sensor_selector,
                    vol.Required(
                        CONF_CLIP_DURATION, default=cfg.get(CONF_CLIP_DURATION, DEFAULT_CLIP_DURATION)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10, max=120, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_LOOKBACK, default=cfg.get(CONF_LOOKBACK, DEFAULT_LOOKBACK)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=10, step=1, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                    vol.Required(
                        CONF_POST_ROLL, default=cfg.get(CONF_POST_ROLL, DEFAULT_POST_ROLL)
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=60, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="seconds"
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "camera_name": self._current_camera_name,
                "sensor_count": str(len(self._available_sensors)),
            },
        )

    async def async_step_remove_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Remove a camera."""
        current = self._config_entry.data
        cameras = current.get(CONF_CAMERAS, {})

        if not cameras:
            return self.async_abort(reason="no_cameras")

        if user_input is not None:
            camera_to_remove = user_input.get("camera_name")
            if camera_to_remove and camera_to_remove in cameras:
                new_data = {**current}
                new_cameras = dict(cameras)
                del new_cameras[camera_to_remove]
                new_data[CONF_CAMERAS] = new_cameras
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
            return self.async_create_entry(title="", data={})

        camera_names = {name: name for name in cameras}

        return self.async_show_form(
            step_id="remove_camera",
            data_schema=vol.Schema(
                {
                    vol.Required("camera_name"): vol.In(camera_names),
                }
            ),
        )
