"""Glue between Home Assistant states and the NILM engine."""
from __future__ import annotations

from datetime import timedelta
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GRID_POWER,
    CONF_INVERT_REACTIVE,
    CONF_POWER_FACTOR,
    CONF_PV_POWER,
    CONF_REACTIVE_POWER,
    CONF_SUBMETERS,
    CONF_VARIABLE_ENTITIES,
    CONF_VARIABLE_NAME,
    CONF_VARIABLE_OFF_DELAY,
    DEFAULTS,
    DOMAIN,
    EVENT_UNKNOWN_LOAD,
    INACTIVE_STATES,
    SAVE_DELAY,
    SIGNAL_SIGNATURE_ADDED,
    SIGNAL_UPDATE,
    STORAGE_KEY,
    TICK_SECONDS,
)
from .engine import STORAGE_VERSION, EngineConfig, Event as NilmEvent, NilmEngine

_LOGGER = logging.getLogger(__name__)

_POWER_SCALE = {
    UnitOfPower.WATT: 1.0,
    UnitOfPower.KILO_WATT: 1000.0,
    "mW": 0.001,
    "MW": 1_000_000.0,
}


class NilmCoordinator:
    """Owns the engine for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.conf: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self.engine = NilmEngine(
            EngineConfig.from_dict(self.conf), hour_of=self._hour_of
        )
        self.store: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )
        self.selected_event_id: str | None = None
        self.signal_update = SIGNAL_UPDATE.format(entry.entry_id)
        self.signal_added = SIGNAL_SIGNATURE_ADDED.format(entry.entry_id)
        self.last_inputs: dict[str, Any] = {}
        self._variable_last_on: float = 0.0
        self._last_push: float = 0.0
        self._unsubs: list = []

    # ------------------------------------------------------------------
    @property
    def variable_name(self) -> str:
        return self.conf.get(CONF_VARIABLE_NAME) or DEFAULTS[CONF_VARIABLE_NAME]

    @property
    def has_variable(self) -> bool:
        return bool(self.conf.get(CONF_VARIABLE_ENTITIES))

    @staticmethod
    def _hour_of(ts: float) -> int:
        return dt_util.as_local(dt_util.utc_from_timestamp(ts)).hour

    def format_ts(self, ts: float) -> str:
        return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%d/%m %H:%M")

    def iso(self, ts: float | None) -> str | None:
        if ts is None:
            return None
        return dt_util.utc_from_timestamp(ts).isoformat()

    def _inputs(self) -> list[str]:
        ids = [self.conf[CONF_GRID_POWER]]
        for key in (CONF_PV_POWER, CONF_REACTIVE_POWER, CONF_POWER_FACTOR):
            if self.conf.get(key):
                ids.append(self.conf[key])
        ids += list(self.conf.get(CONF_SUBMETERS) or [])
        ids += list(self.conf.get(CONF_VARIABLE_ENTITIES) or [])
        return ids

    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        data = await self.store.async_load()
        try:
            self.engine.load_dict(data)
        except Exception:  # pragma: no cover - corrupted storage
            _LOGGER.exception("Could not load stored NILM data, starting empty")
        self.engine.add_listener(self._engine_event)

        self._unsubs.append(
            async_track_state_change_event(self.hass, self._inputs(), self._on_change)
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS)
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_stop)
        )
        self._feed(time.time())

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001 - the stop listener may have fired
                pass
        self._unsubs.clear()
        await self.store.async_save(self.engine.to_dict())

    async def _on_stop(self, _event: Event) -> None:
        await self.store.async_save(self.engine.to_dict())

    def schedule_save(self) -> None:
        self.store.async_delay_save(self.engine.to_dict, SAVE_DELAY)

    # ------------------------------------------------------------------
    # Reading states
    # ------------------------------------------------------------------
    def _power(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state: State | None = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        unit = state.attributes.get("unit_of_measurement")
        return value * _POWER_SCALE.get(unit, 1.0)

    def _number(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _is_active(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        value = str(state.state).lower()
        if value in INACTIVE_STATES:
            return False
        try:
            return float(value) > 0
        except ValueError:
            return True

    def _reactive(self, grid: float) -> float | None:
        """House reactive power, only while importing from the grid."""
        if grid <= 30:
            return None
        sign = -1.0 if self.conf.get(CONF_INVERT_REACTIVE) else 1.0
        if self.conf.get(CONF_REACTIVE_POWER):
            q = self._number(self.conf[CONF_REACTIVE_POWER])
            return None if q is None else sign * q
        if self.conf.get(CONF_POWER_FACTOR):
            pf = self._number(self.conf[CONF_POWER_FACTOR])
            if pf is None:
                return None
            if abs(pf) > 1.0:  # percent
                pf /= 100.0
            if pf == 0 or abs(pf) > 1.0:
                return None
            q = grid * math.sqrt(max(1.0 - pf * pf, 0.0)) / abs(pf)
            return sign * math.copysign(q, pf)
        return None

    def _feed(self, ts: float, new_reading: bool = True) -> None:
        grid = self._power(self.conf[CONF_GRID_POWER])
        if grid is None:
            return
        pv = self._power(self.conf.get(CONF_PV_POWER)) or 0.0
        house = grid + max(pv, 0.0)
        measured = {
            e: self._power(e) or 0.0 for e in self.conf.get(CONF_SUBMETERS) or []
        }
        residual = house - sum(measured.values())
        q = self._reactive(grid)

        variable = None
        if self.has_variable:
            now_on = any(self._is_active(e) for e in self.conf[CONF_VARIABLE_ENTITIES])
            if now_on:
                self._variable_last_on = ts
            delay = float(self.conf.get(CONF_VARIABLE_OFF_DELAY) or 0)
            variable = now_on or (ts - self._variable_last_on) < delay

        self.last_inputs = {
            "house_power": round(house, 1),
            "measured_power": round(sum(measured.values()), 1),
            "unmeasured_power": round(residual, 1),
            "reactive_power": round(q, 1) if q is not None else None,
            "variable_active": variable,
        }
        if new_reading:
            self.engine.update(ts, residual, q, variable)
        else:
            if variable is not None:
                self.engine.variable_active = variable
            self.engine.tick(ts)

    @callback
    def _on_change(self, _event: Event) -> None:
        self._feed(time.time())

    @callback
    def _on_tick(self, _now) -> None:
        self._feed(time.time(), new_reading=False)

    # ------------------------------------------------------------------
    # Engine notifications
    # ------------------------------------------------------------------
    @callback
    def _engine_event(self, kind: str, payload: Any) -> None:
        if kind == "signature_added":
            async_dispatcher_send(self.hass, self.signal_added, payload)
            self.schedule_save()
        elif kind == "signature_removed":
            self._remove_entities(payload)
            self.schedule_save()
        elif kind == "event":
            self.schedule_save()
            ev: NilmEvent = payload
            if ev.unknown and ev.end_ts is None and ev.label is None:
                self.hass.bus.async_fire(EVENT_UNKNOWN_LOAD, self.event_dict(ev))
        elif kind == "update":
            # plain readings: refresh entities at most every few seconds
            now = time.monotonic()
            if now - self._last_push < TICK_SECONDS:
                return
            self._last_push = now
        async_dispatcher_send(self.hass, self.signal_update)

    def _remove_entities(self, name: str) -> None:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        for suffix in ("power", "energy"):
            uid = f"{self.entry.entry_id}_{appliance_key(name)}_{suffix}"
            entity_id = reg.async_get_entity_id("sensor", DOMAIN, uid)
            if entity_id:
                reg.async_remove(entity_id)

    # ------------------------------------------------------------------
    # Presentation helpers
    # ------------------------------------------------------------------
    def event_dict(self, ev: NilmEvent) -> dict[str, Any]:
        dur = ev.duration_s
        return {
            "id": ev.id,
            "time": self.format_ts(ev.ts),
            "start": self.iso(ev.ts),
            "end": self.iso(ev.end_ts),
            "kind": ev.kind,
            "power_w": round(ev.power),
            "reactive_ratio": round(ev.q_ratio, 2) if ev.q_ratio is not None else None,
            "duration_min": round(dur / 60, 1) if dur else None,
            "matched": ev.appliance,
            "label": ev.label,
            "during_variable": ev.during_variable,
        }

    def event_option(self, ev: NilmEvent) -> str:
        if ev.label:
            tag = f"✓ {ev.label}"
        elif ev.appliance:
            tag = f"≈ {ev.appliance}"
        else:
            tag = "?"
        sign = "+" if ev.kind == "on" else "~"
        return f"{self.format_ts(ev.ts)} · {sign}{round(ev.power)} W · {tag} · {ev.id}"

    def selectable_events(self) -> list[NilmEvent]:
        out = [
            e
            for e in reversed(self.engine.events)
            if not e.dismissed and not e.during_variable
        ]
        return out[:25]


def appliance_key(name: str) -> str:
    from homeassistant.util import slugify

    return slugify(name) or "appliance"
