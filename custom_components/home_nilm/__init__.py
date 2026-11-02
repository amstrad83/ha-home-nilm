"""Home NILM: learn and recognise appliances from a single power meter."""
from __future__ import annotations

try:  # Home Assistant moved from voluptuous to probatio
    import voluptuous as vol
except ImportError:  # pragma: no cover
    import probatio as vol  # type: ignore[no-redef]

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_EVENT_ID,
    ATTR_HOURS,
    ATTR_NAME,
    ATTR_NEW_NAME,
    ATTR_POWER,
    ATTR_REACTIVE_RATIO,
    ATTR_SAMPLES,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import NilmCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_BASE = {vol.Optional(ATTR_ENTRY_ID): cv.string}

SCHEMAS = {
    "label_event": vol.Schema(
        {**_BASE, vol.Required(ATTR_EVENT_ID): cv.string, vol.Required(ATTR_NAME): cv.string}
    ),
    "label_now": vol.Schema({**_BASE, vol.Required(ATTR_NAME): cv.string}),
    "dismiss_event": vol.Schema({**_BASE, vol.Required(ATTR_EVENT_ID): cv.string}),
    "teach": vol.Schema(
        {
            **_BASE,
            vol.Required(ATTR_NAME): cv.string,
            vol.Required(ATTR_POWER): vol.All(vol.Coerce(float), vol.Range(min=1)),
            vol.Optional(ATTR_REACTIVE_RATIO): vol.Coerce(float),
            vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(float), vol.Range(min=0)),
            vol.Optional(ATTR_HOURS): vol.All(
                cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=23))]
            ),
            vol.Optional(ATTR_SAMPLES, default=1): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=20)
            ),
        }
    ),
    "forget_appliance": vol.Schema({**_BASE, vol.Required(ATTR_NAME): cv.string}),
    "rename_appliance": vol.Schema(
        {**_BASE, vol.Required(ATTR_NAME): cv.string, vol.Required(ATTR_NEW_NAME): cv.string}
    ),
    "get_signatures": vol.Schema(_BASE),
}


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> NilmCoordinator:
    coords: dict[str, NilmCoordinator] = hass.data.get(DOMAIN, {})
    if not coords:
        raise HomeAssistantError("Home NILM is not set up")
    entry_id = call.data.get(ATTR_ENTRY_ID)
    if entry_id:
        if entry_id not in coords:
            raise ServiceValidationError(f"Unknown entry_id {entry_id}")
        return coords[entry_id]
    return next(iter(coords.values()))


async def async_setup(hass: HomeAssistant, _config) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def handle(call: ServiceCall) -> ServiceResponse:
        coord = _coordinator(hass, call)
        eng = coord.engine
        d = call.data
        svc = call.service
        ok = True
        if svc == "label_event":
            ok = eng.label_event(d[ATTR_EVENT_ID], d[ATTR_NAME])
        elif svc == "label_now":
            ok = eng.label_now(d[ATTR_NAME]) is not None
        elif svc == "dismiss_event":
            ok = eng.dismiss_event(d[ATTR_EVENT_ID])
        elif svc == "teach":
            eng.teach(
                d[ATTR_NAME],
                d[ATTR_POWER],
                d.get(ATTR_REACTIVE_RATIO),
                d.get(ATTR_DURATION),
                d.get(ATTR_HOURS),
                d.get(ATTR_SAMPLES, 1),
            )
        elif svc == "forget_appliance":
            ok = eng.forget(d[ATTR_NAME])
        elif svc == "rename_appliance":
            ok = eng.rename(d[ATTR_NAME], d[ATTR_NEW_NAME])
        elif svc == "get_signatures":
            return {
                "signatures": {
                    name: sig.summary() for name, sig in eng.signatures.items()
                },
                "unknown_events": [
                    coord.event_dict(e) for e in eng.unknown_events()
                ],
            }
        if not ok:
            raise ServiceValidationError(
                f"{svc}: nothing to do (unknown event/appliance or nothing on now)"
            )
        coord.schedule_save()
        return None

    for name, schema in SCHEMAS.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handle,
            schema=schema,
            supports_response=(
                SupportsResponse.ONLY if name == "get_signatures" else SupportsResponse.NONE
            ),
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coord = NilmCoordinator(hass, entry)
    await coord.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coord: NilmCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coord.async_stop()
    return ok
