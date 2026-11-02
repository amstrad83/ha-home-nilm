"""Select the event to label from the dashboard."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NOW_OPTION
from .coordinator import NilmCoordinator
from .sensor import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: NilmCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EventSelect(coord)])


class EventSelect(SelectEntity):
    """Options: "now" plus the latest events; the id is the last token."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "event_to_label"
    _attr_icon = "mdi:tag-search-outline"

    def __init__(self, coord: NilmCoordinator) -> None:
        self.coord = coord
        self._attr_unique_id = f"{coord.entry.entry_id}_event_to_label"
        self._attr_device_info = device_info(coord)

    def _options_map(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {NOW_OPTION: None}
        for ev in self.coord.selectable_events():
            out[self.coord.event_option(ev)] = ev.id
        return out

    @property
    def options(self) -> list[str]:
        return list(self._options_map())

    @property
    def current_option(self) -> str | None:
        sel = self.coord.selected_event_id
        if sel is None:
            return NOW_OPTION
        for option, ev_id in self._options_map().items():
            if ev_id == sel:
                return option
        self.coord.selected_event_id = None
        return NOW_OPTION

    async def async_select_option(self, option: str) -> None:
        self.coord.selected_event_id = self._options_map().get(option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coord.signal_update, self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
