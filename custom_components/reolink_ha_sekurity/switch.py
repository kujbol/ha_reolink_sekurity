"""Switch platform for Reolink HA Sekurity alarm toggles."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Reolink HA Sekurity switches from config entry."""
    
    # We create two alarm switches: Full Alarm and Night Alarm
    switches = [
        SekurityAlarmSwitch(
            hass, 
            config_entry.entry_id, 
            "full_alarm", 
            "Sekurity Full Alarm"
        ),
        SekurityAlarmSwitch(
            hass, 
            config_entry.entry_id, 
            "night_alarm", 
            "Sekurity Night Alarm"
        )
    ]
    
    async_add_entities(switches)


class SekurityAlarmSwitch(SwitchEntity, RestoreEntity):
    """A switch that toggles an alarm state and restores its previous state."""

    def __init__(self, hass: HomeAssistant, entry_id: str, key: str, name: str) -> None:
        """Initialize the switch."""
        self.hass = hass
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{key}"
        self._key = key
        
        # This gives us entity_id like switch.reolink_ha_sekurity_full_alarm
        self.entity_id = f"switch.{DOMAIN}_{key}"
        
        self._attr_icon = "mdi:shield-home"
        self._state = False

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        self._state = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        
        # Restore the previous state if available
        last_state = await self.async_get_last_state()
        if last_state:
            self._state = last_state.state == "on"
