"""Sensors: one power + one energy sensor per learned appliance, plus totals."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NilmCoordinator, appliance_key
from .engine import NilmEngine


def device_info(coord: NilmCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coord.entry.entry_id)},
        name=coord.entry.title,
        manufacturer="Home NILM",
        model="Load disaggregation",
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord: NilmCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        OutputPowerSensor(coord, NilmEngine.BASE, "base_load"),
        OutputPowerSensor(coord, NilmEngine.OTHER, "other"),
        OutputEnergySensor(coord, NilmEngine.BASE, "base_load_energy"),
        OutputEnergySensor(coord, NilmEngine.OTHER, "other_energy"),
        UnmeasuredSensor(coord),
        UnknownEventsSensor(coord),
        LastEventSensor(coord),
    ]
    if coord.has_variable:
        entities += [
            OutputPowerSensor(coord, NilmEngine.VARIABLE, None, coord.variable_name),
            OutputEnergySensor(
                coord, NilmEngine.VARIABLE, None, f"{coord.variable_name} energy"
            ),
        ]
    for name in coord.engine.signatures:
        entities += [AppliancePowerSensor(coord, name), ApplianceEnergySensor(coord, name)]
    async_add_entities(entities)

    @callback
    def _added(name: str) -> None:
        async_add_entities(
            [AppliancePowerSensor(coord, name), ApplianceEnergySensor(coord, name)]
        )

    entry.async_on_unload(async_dispatcher_connect(hass, coord.signal_added, _added))


class NilmEntity(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coord: NilmCoordinator) -> None:
        self.coord = coord
        self._attr_device_info = device_info(coord)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coord.signal_update, self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


# ---------------------------------------------------------------------------
# Per appliance
# ---------------------------------------------------------------------------
class AppliancePowerSensor(NilmEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:flash"

    def __init__(self, coord: NilmCoordinator, name: str) -> None:
        super().__init__(coord)
        self.appliance = name
        self._attr_name = name
        self._attr_unique_id = f"{coord.entry.entry_id}_{appliance_key(name)}_power"

    @property
    def available(self) -> bool:
        return self.appliance in self.coord.engine.signatures

    @property
    def native_value(self) -> float | None:
        if not self.available:
            return None
        return round(self.coord.engine.powers().get(self.appliance, 0.0), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        sig = self.coord.engine.signatures.get(self.appliance)
        return sig.summary() if sig else {}


class ApplianceEnergySensor(NilmEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coord: NilmCoordinator, name: str) -> None:
        super().__init__(coord)
        self.appliance = name
        self._attr_name = f"{name} energy"
        self._attr_unique_id = f"{coord.entry.entry_id}_{appliance_key(name)}_energy"

    @property
    def available(self) -> bool:
        return self.appliance in self.coord.engine.signatures

    @property
    def native_value(self) -> float | None:
        if not self.available:
            return None
        return round(self.coord.engine.energy_kwh.get(self.appliance, 0.0), 3)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------
class OutputPowerSensor(NilmEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coord: NilmCoordinator,
        key: str,
        translation_key: str | None,
        name: str | None = None,
    ) -> None:
        super().__init__(coord)
        self.key = key
        if translation_key:
            self._attr_translation_key = translation_key
        else:
            self._attr_name = name
        self._attr_unique_id = f"{coord.entry.entry_id}_{key.strip('_')}_power"

    @property
    def native_value(self) -> float:
        return round(self.coord.engine.powers().get(self.key, 0.0), 1)


class OutputEnergySensor(NilmEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coord: NilmCoordinator,
        key: str,
        translation_key: str | None,
        name: str | None = None,
    ) -> None:
        super().__init__(coord)
        self.key = key
        if translation_key:
            self._attr_translation_key = translation_key
        else:
            self._attr_name = name
        self._attr_unique_id = f"{coord.entry.entry_id}_{key.strip('_')}_energy"

    @property
    def native_value(self) -> float:
        return round(self.coord.engine.energy_kwh.get(self.key, 0.0), 3)


class UnmeasuredSensor(NilmEntity):
    """The signal the engine works on (house minus sub-meters)."""

    _attr_translation_key = "unmeasured"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coord: NilmCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.entry.entry_id}_unmeasured_power"

    @property
    def native_value(self) -> float:
        return round(self.coord.engine.signal, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        eng = self.coord.engine
        return {
            **self.coord.last_inputs,
            "base_load": round(eng.base_load, 1) if eng.base_load is not None else None,
            "tracked_loads": len(eng.active),
            "steps_detected": eng.steps_detected,
            "unpaired_off_steps": eng.unpaired_off,
        }


class UnknownEventsSensor(NilmEntity):
    _attr_translation_key = "unknown_events"
    _attr_icon = "mdi:help-circle-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: NilmCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.entry.entry_id}_unknown_events"

    @property
    def native_value(self) -> int:
        return len(self.coord.engine.unknown_events())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "events": [
                self.coord.event_dict(e) for e in self.coord.engine.unknown_events()[:20]
            ]
        }


class LastEventSensor(NilmEntity):
    _attr_translation_key = "last_event"
    _attr_icon = "mdi:transit-connection-variant"

    def __init__(self, coord: NilmCoordinator) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{coord.entry.entry_id}_last_event"

    @property
    def native_value(self) -> str | None:
        events = self.coord.engine.events
        if not events:
            return None
        ev = events[-1]
        return ev.label or ev.appliance or "?"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        events = self.coord.engine.events
        if not events:
            return {}
        return {
            **self.coord.event_dict(events[-1]),
            "recent": [self.coord.event_dict(e) for e in list(events)[-10:][::-1]],
        }
