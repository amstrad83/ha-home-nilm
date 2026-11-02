"""Type the appliance name: it labels the selected event (or what is on now)."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NilmCoordinator
from .sensor import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: NilmCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LabelText(coord)])


class LabelText(TextEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "appliance_name"
    _attr_icon = "mdi:tag-text-outline"
    _attr_native_max = 60
    _attr_native_value = ""

    def __init__(self, coord: NilmCoordinator) -> None:
        self.coord = coord
        self._attr_unique_id = f"{coord.entry.entry_id}_appliance_name"
        self._attr_device_info = device_info(coord)

    async def async_set_value(self, value: str) -> None:
        name = value.strip()
        if not name:
            return
        eng = self.coord.engine
        ev_id = self.coord.selected_event_id
        if ev_id:
            ok = eng.label_event(ev_id, name)
        else:
            ok = eng.label_now(name) is not None
        if not ok:
            raise ServiceValidationError(
                "Nothing to label: the event is gone or nothing unknown is on now"
            )
        self.coord.selected_event_id = None
        self.coord.schedule_save()
        # the field is an input: it goes back to empty after use
        self._attr_native_value = ""
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, self.coord.signal_update)
