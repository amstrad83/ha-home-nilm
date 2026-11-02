"""Config and options flow for Home NILM."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector as sel

from .const import (
    CONF_GRID_POWER,
    CONF_HOUR_WEIGHT,
    CONF_INVERT_REACTIVE,
    CONF_MATCH_SIGMA,
    CONF_PERSISTENCE,
    CONF_POWER_FACTOR,
    CONF_PV_POWER,
    CONF_REACTIVE_POWER,
    CONF_STEP_THRESHOLD,
    CONF_SUBMETERS,
    CONF_SUSTAINED_MINUTES,
    CONF_SUSTAINED_POWER,
    CONF_VARIABLE_ENTITIES,
    CONF_VARIABLE_NAME,
    CONF_VARIABLE_OFF_DELAY,
    DEFAULTS,
    DOMAIN,
)

_POWER = sel.EntitySelector(
    sel.EntitySelectorConfig(domain="sensor", device_class="power")
)
_POWER_MULTI = sel.EntitySelector(
    sel.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
)
_SENSOR = sel.EntitySelector(sel.EntitySelectorConfig(domain="sensor"))
_ANY_MULTI = sel.EntitySelector(sel.EntitySelectorConfig(multiple=True))


def _num(minimum: float, maximum: float, step: float, unit: str | None = None):
    return sel.NumberSelector(
        sel.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=sel.NumberSelectorMode.BOX,
        )
    )


def _opt(key: str, values: dict[str, Any]):
    """Optional field that keeps the current value as suggestion."""
    if values.get(key) not in (None, "", []):
        return vol.Optional(key, description={"suggested_value": values[key]})
    return vol.Optional(key)


def _sources_schema(values: dict[str, Any], with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=values.get(CONF_NAME, "Home NILM"))] = str
    fields[vol.Required(CONF_GRID_POWER, default=values.get(CONF_GRID_POWER, vol.UNDEFINED))] = _POWER
    fields[_opt(CONF_PV_POWER, values)] = _POWER
    fields[_opt(CONF_SUBMETERS, values)] = _POWER_MULTI
    fields[_opt(CONF_POWER_FACTOR, values)] = _SENSOR
    fields[_opt(CONF_REACTIVE_POWER, values)] = _SENSOR
    fields[
        vol.Optional(
            CONF_INVERT_REACTIVE,
            default=values.get(CONF_INVERT_REACTIVE, DEFAULTS[CONF_INVERT_REACTIVE]),
        )
    ] = sel.BooleanSelector()
    fields[_opt(CONF_VARIABLE_ENTITIES, values)] = _ANY_MULTI
    fields[
        vol.Optional(
            CONF_VARIABLE_NAME,
            default=values.get(CONF_VARIABLE_NAME, DEFAULTS[CONF_VARIABLE_NAME]),
        )
    ] = str
    return vol.Schema(fields)


def _tuning_schema(values: dict[str, Any]) -> vol.Schema:
    def d(key):
        return values.get(key, DEFAULTS[key])

    return vol.Schema(
        {
            vol.Required(CONF_STEP_THRESHOLD, default=d(CONF_STEP_THRESHOLD)): _num(20, 1000, 5, "W"),
            vol.Required(CONF_PERSISTENCE, default=d(CONF_PERSISTENCE)): _num(0, 120, 1, "s"),
            vol.Required(CONF_MATCH_SIGMA, default=d(CONF_MATCH_SIGMA)): _num(1, 6, 0.1),
            vol.Required(CONF_HOUR_WEIGHT, default=d(CONF_HOUR_WEIGHT)): _num(0, 1, 0.1),
            vol.Required(CONF_SUSTAINED_POWER, default=d(CONF_SUSTAINED_POWER)): _num(20, 5000, 10, "W"),
            vol.Required(CONF_SUSTAINED_MINUTES, default=d(CONF_SUSTAINED_MINUTES)): _num(1, 240, 1, "min"),
            vol.Required(CONF_VARIABLE_OFF_DELAY, default=d(CONF_VARIABLE_OFF_DELAY)): _num(0, 3600, 10, "s"),
        }
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    subs = user_input.get(CONF_SUBMETERS) or []
    if user_input.get(CONF_GRID_POWER) in subs:
        errors[CONF_SUBMETERS] = "grid_in_submeters"
    if user_input.get(CONF_POWER_FACTOR) and user_input.get(CONF_REACTIVE_POWER):
        errors[CONF_REACTIVE_POWER] = "pf_and_reactive"
    return errors


class HomeNilmConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                self._data = user_input
                return await self.async_step_tuning()
        return self.async_show_form(
            step_id="user",
            data_schema=_sources_schema(user_input or {}, with_name=True),
            errors=errors,
        )

    async def async_step_tuning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            name = self._data.pop(CONF_NAME, "Home NILM")
            return self.async_create_entry(title=name, data=self._data, options=user_input)
        return self.async_show_form(step_id="tuning", data_schema=_tuning_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HomeNilmOptionsFlow()


class HomeNilmOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._sources: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                # empty optional fields must clear the previous value
                for key in (
                    CONF_PV_POWER,
                    CONF_SUBMETERS,
                    CONF_POWER_FACTOR,
                    CONF_REACTIVE_POWER,
                    CONF_VARIABLE_ENTITIES,
                ):
                    user_input.setdefault(key, [] if key in (CONF_SUBMETERS, CONF_VARIABLE_ENTITIES) else None)
                self._sources = user_input
                return await self.async_step_tuning()
        return self.async_show_form(
            step_id="init",
            data_schema=_sources_schema(user_input or self._current, with_name=False),
            errors=errors,
        )

    async def async_step_tuning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._sources, **user_input})
        return self.async_show_form(
            step_id="tuning", data_schema=_tuning_schema(self._current)
        )
