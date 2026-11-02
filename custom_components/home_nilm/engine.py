"""Home NILM engine.

Pure Python (no Home Assistant imports) so that it can be unit tested and
replayed against recorded history.

The engine receives a single "unexplained" power signal (house consumption
minus every sub-metered load) and, optionally, the matching reactive power.
It then:

* detects steps (appliance switching on/off) with a persistence filter;
* pairs every "off" step with the "on" step it most likely closes;
* classifies "on" steps against learned signatures (active power,
  reactive/active ratio, duration, usual hours of use);
* keeps per-appliance power and energy, a rolling base load and the
  remaining "other" power;
* lists the events it could not recognise so that the user can label them.
  Every label updates the matching signature (online learning).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
import uuid
from typing import Any, Callable

STORAGE_VERSION = 1


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class EngineConfig:
    """Tunable parameters."""

    step_threshold: float = 80.0  # W, minimum step that is an event
    persistence_s: float = 10.0  # s, a step must last this long
    stability_w: float = 40.0  # W, readings within this band are "the same"
    match_sigma: float = 3.0  # max distance (in std devs) for a match
    pair_tolerance_frac: float = 0.25  # off step vs on step, relative
    pair_tolerance_w: float = 40.0  # off step vs on step, absolute
    max_on_hours: float = 12.0  # open loads older than this are dropped
    sustained_power: float = 150.0  # W of "other" that triggers an event
    sustained_minutes: float = 15.0
    hour_weight: float = 0.5  # 0 = ignore hour of day, 1 = strong prior
    base_percentile: float = 5.0  # base load = this percentile over 24 h
    max_events: int = 60
    min_reactive_step: float = 50.0  # W, below this Q/P is too noisy

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EngineConfig":
        cfg = cls()
        for key, value in (data or {}).items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, type(getattr(cfg, key))(value))
        return cfg


# --------------------------------------------------------------------------
# Running statistics
# --------------------------------------------------------------------------
@dataclass
class RunningStat:
    """Welford mean/variance with optional cap on the effective sample size.

    The cap makes old samples fade so the signature follows the appliance
    if it changes (a new fridge, a different program...).
    """

    n: float = 0.0
    mean: float = 0.0
    m2: float = 0.0
    cap: float = 50.0

    def add(self, x: float, weight: float = 1.0) -> None:
        if self.n >= self.cap:
            # keep the variance estimate but give the new sample a fixed weight
            scale = (self.cap - weight) / self.n
            self.n *= scale
            self.m2 *= scale
        self.n += weight
        delta = x - self.mean
        self.mean += delta * weight / self.n
        self.m2 += weight * delta * (x - self.mean)

    @property
    def std(self) -> float | None:
        if self.n < 2:
            return None
        return math.sqrt(max(self.m2 / (self.n - 1), 0.0))

    def to_dict(self) -> dict[str, float]:
        return {"n": self.n, "mean": self.mean, "m2": self.m2}

    @classmethod
    def from_dict(cls, data: dict[str, float] | None) -> "RunningStat":
        stat = cls()
        if data:
            stat.n = float(data.get("n", 0))
            stat.mean = float(data.get("mean", 0))
            stat.m2 = float(data.get("m2", 0))
        return stat


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------
@dataclass
class Signature:
    """What a given appliance looks like when it switches on."""

    name: str
    power: RunningStat = field(default_factory=RunningStat)
    q_ratio: RunningStat = field(default_factory=RunningStat)
    log_duration: RunningStat = field(default_factory=RunningStat)
    hours: list[float] = field(default_factory=lambda: [0.0] * 24)

    # -- learning ----------------------------------------------------------
    def learn(
        self,
        power: float | None = None,
        q_ratio: float | None = None,
        duration_s: float | None = None,
        hour: int | None = None,
        weight: float = 1.0,
    ) -> None:
        if power is not None and power > 0:
            self.power.add(power, weight)
        if q_ratio is not None:
            self.q_ratio.add(q_ratio, weight)
        if duration_s is not None and duration_s > 0:
            self.log_duration.add(math.log(duration_s), weight)
        if hour is not None:
            self.hours[hour % 24] += weight

    # -- matching ----------------------------------------------------------
    def distance(
        self,
        power: float,
        q_ratio: float | None,
        duration_s: float | None,
        hour: int | None,
        hour_weight: float,
    ) -> tuple[float, float]:
        """Return (normalised distance, worst single-feature z).

        Distance is the mean squared z-score over the available features plus
        a soft penalty/bonus for the hour of day.
        """
        if self.power.n <= 0:
            return math.inf, math.inf
        std_p = self.power.std
        floor_p = max(0.06 * self.power.mean, 15.0)
        prior_p = max(0.12 * self.power.mean, 25.0)
        sp = max(std_p, floor_p) if std_p is not None else prior_p
        zs = [(power - self.power.mean) / sp]

        if q_ratio is not None and self.q_ratio.n > 0:
            sq = self.q_ratio.std
            sq = max(sq, 0.08) if sq is not None else 0.12
            zs.append((q_ratio - self.q_ratio.mean) / sq)

        if duration_s is not None and duration_s > 0 and self.log_duration.n > 0:
            sd = self.log_duration.std
            sd = max(sd, 0.35) if sd is not None else 0.6
            zs.append((math.log(duration_s) - self.log_duration.mean) / sd)

        d2 = sum(z * z for z in zs) / len(zs)
        worst = max(abs(z) for z in zs)

        total = sum(self.hours)
        if hour is not None and total >= 3 and hour_weight > 0:
            p_hour = (self.hours[hour % 24] + 0.5) / (total + 12.0)
            # log-ratio against a uniform prior, bounded to +-1.5
            penalty = -math.log(p_hour * 24.0)
            d2 += hour_weight * max(-1.5, min(1.5, penalty))
        return d2, worst

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "power": self.power.to_dict(),
            "q_ratio": self.q_ratio.to_dict(),
            "log_duration": self.log_duration.to_dict(),
            "hours": self.hours,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Signature":
        sig = cls(name=data["name"])
        sig.power = RunningStat.from_dict(data.get("power"))
        sig.q_ratio = RunningStat.from_dict(data.get("q_ratio"))
        sig.log_duration = RunningStat.from_dict(data.get("log_duration"))
        hours = data.get("hours") or []
        if len(hours) == 24:
            sig.hours = [float(h) for h in hours]
        return sig

    def summary(self) -> dict[str, Any]:
        dur = (
            round(math.exp(self.log_duration.mean) / 60, 1)
            if self.log_duration.n
            else None
        )
        return {
            "power_w": round(self.power.mean),
            "power_std_w": round(self.power.std) if self.power.std else None,
            "reactive_ratio": round(self.q_ratio.mean, 2) if self.q_ratio.n else None,
            "typical_minutes": dur,
            "samples": round(self.power.n),
        }


# --------------------------------------------------------------------------
# Events and active loads
# --------------------------------------------------------------------------
@dataclass
class Event:
    id: str
    ts: float
    kind: str  # "on" | "sustained" | "manual"
    power: float
    dq: float | None = None
    appliance: str | None = None  # name the engine matched
    label: str | None = None  # name the user gave
    end_ts: float | None = None
    dismissed: bool = False
    during_variable: bool = False

    @property
    def q_ratio(self) -> float | None:
        if self.dq is None or self.power <= 0:
            return None
        return self.dq / self.power

    @property
    def duration_s(self) -> float | None:
        if self.end_ts is None:
            return None
        return self.end_ts - self.ts

    @property
    def unknown(self) -> bool:
        return (
            self.appliance is None
            and self.label is None
            and not self.dismissed
            and not self.during_variable
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "kind": self.kind,
            "power": self.power,
            "dq": self.dq,
            "appliance": self.appliance,
            "label": self.label,
            "end_ts": self.end_ts,
            "dismissed": self.dismissed,
            "during_variable": self.during_variable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__ if k in data})


@dataclass
class ActiveLoad:
    event_id: str
    appliance: str | None
    power: float
    dq: float | None
    start_ts: float


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class NilmEngine:
    """Online step-based disaggregation with learned signatures."""

    def __init__(
        self,
        config: EngineConfig | None = None,
        hour_of: Callable[[float], int] | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self._hour_of = hour_of or (lambda ts: time.localtime(ts).tm_hour)

        self.signatures: dict[str, Signature] = {}
        self.events: deque[Event] = deque(maxlen=self.config.max_events)
        self.active: list[ActiveLoad] = []
        self.energy_kwh: dict[str, float] = {}

        # live signal
        self.signal: float = 0.0
        self.reactive: float | None = None
        self.variable_active: bool = False
        self._last_ts: float | None = None

        # step detector state
        self._level_p: float | None = None
        self._level_q: float | None = None
        self._cand_p: float | None = None
        self._cand_q: float | None = None
        self._cand_ts: float = 0.0
        self._cand_n: int = 0

        # base load (one sample per minute, 24 h)
        self._minute_samples: deque[float] = deque(maxlen=1440)
        self._minute_acc: list[float] = []
        self._minute_start: float | None = None
        self.base_load: float | None = None

        # sustained "other" detector
        self._sustained_since: float | None = None
        self._sustained_acc: list[float] = []
        self._last_sustained_ts: float = 0.0

        # counters for diagnostics
        self.steps_detected = 0
        self.unpaired_off = 0

        # listeners: called with (kind, payload)
        self._listeners: list[Callable[[str, Any], None]] = []

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------
    def add_listener(self, cb: Callable[[str, Any], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _emit(self, kind: str, payload: Any = None) -> None:
        for cb in list(self._listeners):
            cb(kind, payload)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def update(
        self,
        ts: float,
        power: float,
        reactive: float | None = None,
        variable_active: bool | None = None,
        new_reading: bool = True,
    ) -> None:
        """Feed a new value of the unexplained power signal."""
        if variable_active is not None:
            self.variable_active = variable_active
        self._integrate(ts)
        self.signal = max(power, 0.0)
        self.reactive = reactive
        self._detect(ts, self.signal, reactive, new_reading)
        self._expire(ts)
        self._track_base(ts, self.signal)
        self._track_sustained(ts)
        self._emit("update")

    def tick(self, ts: float) -> None:
        """Re-evaluate without a new reading (sensors that did not change)."""
        if self._last_ts is None:
            return
        self.update(ts, self.signal, self.reactive, None, new_reading=False)

    # ------------------------------------------------------------------
    # Step detection
    # ------------------------------------------------------------------
    def _detect(
        self, ts: float, p: float, q: float | None, new_reading: bool
    ) -> None:
        cfg = self.config
        if self._level_p is None:
            self._level_p, self._level_q = p, q
            return

        if abs(p - self._level_p) < cfg.step_threshold:
            # back near the current level: drop any candidate, follow drift
            self._cand_p = None
            if new_reading:
                self._level_p += 0.2 * (p - self._level_p)
                if q is not None:
                    self._level_q = (
                        q if self._level_q is None
                        else self._level_q + 0.2 * (q - self._level_q)
                    )
            return

        if self._cand_p is None or abs(p - self._cand_p) > cfg.stability_w:
            if new_reading:
                self._cand_p, self._cand_q = p, q
                self._cand_ts, self._cand_n = ts, 1
            return

        if new_reading:
            self._cand_n += 1
            self._cand_p += (p - self._cand_p) / self._cand_n
            if q is not None:
                self._cand_q = (
                    q if self._cand_q is None
                    else self._cand_q + (q - self._cand_q) / self._cand_n
                )

        elapsed = ts - self._cand_ts
        confirmed = (self._cand_n >= 2 and elapsed >= cfg.persistence_s) or (
            self._cand_n >= 1 and elapsed >= 3 * cfg.persistence_s
        )
        if not confirmed:
            return

        dp = self._cand_p - self._level_p
        dq = (
            self._cand_q - self._level_q
            if self._cand_q is not None and self._level_q is not None
            else None
        )
        step_ts = self._cand_ts
        self._level_p, self._level_q = self._cand_p, self._cand_q
        self._cand_p = None
        self.steps_detected += 1
        if dp > 0:
            self._on_step(step_ts, dp, dq)
        else:
            self._off_step(step_ts, -dp, -dq if dq is not None else None)

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def _q_ratio(self, power: float, dq: float | None) -> float | None:
        if dq is None or power < self.config.min_reactive_step:
            return None
        return dq / power

    def classify(
        self,
        power: float,
        dq: float | None = None,
        duration_s: float | None = None,
        hour: int | None = None,
    ) -> tuple[str | None, float]:
        """Best matching signature, or (None, distance) if nothing is close."""
        qr = self._q_ratio(power, dq)
        best, best_d = None, math.inf
        for sig in self.signatures.values():
            d2, worst = sig.distance(
                power, qr, duration_s, hour, self.config.hour_weight
            )
            if worst > self.config.match_sigma + 1.0:
                continue
            if d2 < best_d:
                best, best_d = sig.name, d2
        if best is None or best_d > self.config.match_sigma**2:
            return None, best_d
        return best, best_d

    def _on_step(self, ts: float, dp: float, dq: float | None) -> None:
        hour = self._hour_of(ts)
        name, _ = self.classify(dp, dq, None, hour)
        ev = Event(
            id=uuid.uuid4().hex[:10],
            ts=ts,
            kind="on",
            power=round(dp, 1),
            dq=round(dq, 1) if dq is not None else None,
            appliance=name,
            during_variable=self.variable_active and name is None,
        )
        self._add_event(ev)
        self.active.append(ActiveLoad(ev.id, name, dp, dq, ts))
        self._emit("event", ev)

    def _off_step(self, ts: float, dp: float, dq: float | None) -> None:
        cfg = self.config
        best_i, best_score = None, math.inf
        for i, load in enumerate(self.active):
            tol = max(cfg.pair_tolerance_w, cfg.pair_tolerance_frac * load.power)
            err = abs(load.power - dp)
            if err > tol:
                continue
            score = err / tol
            if load.dq is not None and dq is not None and dp >= cfg.min_reactive_step:
                score += min(abs(load.dq - dq) / max(30.0, 0.3 * dp), 2.0) * 0.5
            # prefer the most recent among equals (LIFO for repeated cycles)
            score -= (load.start_ts / 1e12)
            if score < best_score:
                best_i, best_score = i, score
        if best_i is None:
            self.unpaired_off += 1
            return
        load = self.active.pop(best_i)
        ev = self.get_event(load.event_id)
        if ev is None:
            return
        ev.end_ts = ts
        duration = ts - load.start_ts
        name = ev.label or ev.appliance
        if ev.label is None and ev.appliance is None:
            # now that we know the duration, try again
            name2, _ = self.classify(ev.power, ev.dq, duration, self._hour_of(ev.ts))
            if name2:
                ev.appliance = name2
                name = name2
        if name and name in self.signatures and ev.label is None:
            # light self-learning on duration only (power is learned from labels)
            self.signatures[name].learn(duration_s=duration, weight=0.3)
        self._emit("event", ev)

    def _expire(self, ts: float) -> None:
        limit = self.config.max_on_hours * 3600
        before = len(self.active)
        self.active = [a for a in self.active if ts - a.start_ts <= limit]
        # if the confirmed level fell below what we believe is on (a missed
        # off step), drop the oldest loads. Pending candidates are left alone.
        level = self._level_p if self._level_p is not None else self.signal
        excess = self.tracked_power - max(level - (self.base_load or 0) * 0.5, 0)
        while (
            self._cand_p is None
            and self.active
            and excess > self.config.step_threshold
        ):
            dropped = self.active.pop(0)
            excess -= dropped.power
        if len(self.active) != before:
            self._emit("active")

    # ------------------------------------------------------------------
    # Base load / sustained / energy
    # ------------------------------------------------------------------
    def _track_base(self, ts: float, p: float) -> None:
        if self._minute_start is None:
            self._minute_start = ts
        self._minute_acc.append(p)
        if ts - self._minute_start >= 60:
            self._minute_samples.append(sum(self._minute_acc) / len(self._minute_acc))
            self._minute_acc = []
            self._minute_start = ts
            data = sorted(self._minute_samples)
            k = int(round((len(data) - 1) * self.config.base_percentile / 100))
            self.base_load = data[k]

    def _track_sustained(self, ts: float) -> None:
        cfg = self.config
        other = self.other_power
        if other < cfg.sustained_power or self.variable_active:
            self._sustained_since = None
            self._sustained_acc = []
            return
        if self._sustained_since is None:
            self._sustained_since = ts
        self._sustained_acc.append(other)
        if (
            ts - self._sustained_since >= cfg.sustained_minutes * 60
            and ts - self._last_sustained_ts >= 3600
        ):
            ev = Event(
                id=uuid.uuid4().hex[:10],
                ts=self._sustained_since,
                kind="sustained",
                power=round(sum(self._sustained_acc) / len(self._sustained_acc), 1),
            )
            self._last_sustained_ts = ts
            self._add_event(ev)
            self._emit("event", ev)

    def _integrate(self, ts: float) -> None:
        if self._last_ts is not None:
            dt_h = max(ts - self._last_ts, 0) / 3600.0
            if 0 < dt_h < 0.25:  # ignore long gaps (restart)
                for name, power in self.powers().items():
                    if power > 0:
                        self.energy_kwh[name] = (
                            self.energy_kwh.get(name, 0.0) + power * dt_h / 1000.0
                        )
        self._last_ts = ts

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------
    BASE = "__base__"
    OTHER = "__other__"
    VARIABLE = "__variable__"

    @property
    def tracked_power(self) -> float:
        return sum(a.power for a in self.active)

    def appliance_power(self, name: str) -> float:
        return sum(
            a.power for a in self.active if self._load_name(a) == name
        )

    def _load_name(self, load: ActiveLoad) -> str | None:
        ev = self.get_event(load.event_id)
        if ev is not None:
            return ev.label or ev.appliance
        return load.appliance

    @property
    def other_power(self) -> float:
        known = sum(a.power for a in self.active if self._load_name(a))
        return max(self.signal - (self.base_load or 0.0) - known, 0.0)

    def powers(self) -> dict[str, float]:
        """Power of every output, keys are signature names or BASE/OTHER/VARIABLE."""
        out = {name: 0.0 for name in self.signatures}
        for a in self.active:
            n = self._load_name(a)
            if n:
                out[n] = out.get(n, 0.0) + a.power
        # never explain more than we measure
        explained = sum(out.values())
        avail = max(self.signal - (self.base_load or 0.0), 0.0)
        if explained > avail and explained > 0:
            scale = avail / explained
            out = {k: v * scale for k, v in out.items()}
        base = min(self.base_load or 0.0, self.signal)
        other = max(self.signal - base - sum(out.values()), 0.0)
        out[self.BASE] = base
        if self.variable_active:
            out[self.VARIABLE] = other
            out[self.OTHER] = 0.0
        else:
            out[self.VARIABLE] = 0.0
            out[self.OTHER] = other
        return out

    def unknown_events(self) -> list[Event]:
        return [e for e in reversed(self.events) if e.unknown]

    def get_event(self, event_id: str) -> Event | None:
        for e in self.events:
            if e.id == event_id:
                return e
        return None

    def _add_event(self, ev: Event) -> None:
        if len(self.events) == self.events.maxlen:
            old = self.events[0]
            self.active = [a for a in self.active if a.event_id != old.id]
        self.events.append(ev)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    def _signature(self, name: str) -> tuple[Signature, bool]:
        name = name.strip()
        if name in self.signatures:
            return self.signatures[name], False
        sig = Signature(name=name)
        self.signatures[name] = sig
        return sig, True

    def label_event(self, event_id: str, name: str) -> bool:
        """Attach a user label to an event and learn from it."""
        ev = self.get_event(event_id)
        if ev is None or not name.strip():
            return False
        sig, created = self._signature(name)
        name = sig.name
        if ev.kind == "on":
            sig.learn(
                power=ev.power,
                q_ratio=self._q_ratio(ev.power, ev.dq),
                duration_s=ev.duration_s,
                hour=self._hour_of(ev.ts),
            )
        else:
            # sustained / manual: power only, half weight
            sig.learn(power=ev.power, hour=self._hour_of(ev.ts), weight=0.5)
        ev.label = name
        for a in self.active:
            if a.event_id == ev.id:
                a.appliance = name
        if created:
            self._emit("signature_added", name)
        self._emit("event", ev)
        return True

    def label_now(self, name: str, ts: float | None = None) -> str | None:
        """Label what is on right now: the latest unknown open load, or the
        current "other" power as a manual event."""
        ts = ts if ts is not None else (self._last_ts or time.time())
        for a in reversed(self.active):
            ev = self.get_event(a.event_id)
            if ev is not None and ev.label is None and ev.appliance is None:
                self.label_event(ev.id, name)
                return ev.id
        power = self.other_power
        if power < self.config.step_threshold * 0.5:
            return None
        ev = Event(id=uuid.uuid4().hex[:10], ts=ts, kind="manual", power=round(power, 1))
        self._add_event(ev)
        self.label_event(ev.id, name)
        return ev.id

    def dismiss_event(self, event_id: str) -> bool:
        ev = self.get_event(event_id)
        if ev is None:
            return False
        ev.dismissed = True
        self._emit("event", ev)
        return True

    def teach(
        self,
        name: str,
        power: float,
        reactive_ratio: float | None = None,
        duration_minutes: float | None = None,
        hours: list[int] | None = None,
        samples: int = 1,
    ) -> None:
        """Seed or reinforce a signature without an event."""
        sig, created = self._signature(name)
        for _ in range(max(samples, 1)):
            sig.learn(
                power=power,
                q_ratio=reactive_ratio,
                duration_s=duration_minutes * 60 if duration_minutes else None,
            )
        for h in hours or []:
            sig.learn(hour=int(h))
        if created:
            self._emit("signature_added", sig.name)
        self._emit("update")

    def forget(self, name: str) -> bool:
        if name not in self.signatures:
            return False
        del self.signatures[name]
        for e in self.events:
            if e.appliance == name:
                e.appliance = None
            if e.label == name:
                e.label = None
        for a in self.active:
            if a.appliance == name:
                a.appliance = None
        self.energy_kwh.pop(name, None)
        self._emit("signature_removed", name)
        return True

    def rename(self, old: str, new: str) -> bool:
        new = new.strip()
        if old not in self.signatures or not new or new in self.signatures:
            return False
        sig = self.signatures.pop(old)
        sig.name = new
        self.signatures[new] = sig
        for e in self.events:
            if e.appliance == old:
                e.appliance = new
            if e.label == old:
                e.label = new
        for a in self.active:
            if a.appliance == old:
                a.appliance = new
        if old in self.energy_kwh:
            self.energy_kwh[new] = self.energy_kwh.pop(old)
        self._emit("signature_removed", old)
        self._emit("signature_added", new)
        return True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STORAGE_VERSION,
            "signatures": [s.to_dict() for s in self.signatures.values()],
            "events": [e.to_dict() for e in self.events],
            "energy_kwh": self.energy_kwh,
            "base_samples": list(self._minute_samples),
        }

    def load_dict(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        self.signatures = {
            s["name"]: Signature.from_dict(s) for s in data.get("signatures", [])
        }
        self.events = deque(
            (Event.from_dict(e) for e in data.get("events", [])),
            maxlen=self.config.max_events,
        )
        # events still open at shutdown cannot be closed any more
        for e in self.events:
            if e.kind == "on" and e.end_ts is None:
                e.end_ts = e.ts
        self.energy_kwh = {k: float(v) for k, v in (data.get("energy_kwh") or {}).items()}
        self._minute_samples = deque(
            (float(x) for x in data.get("base_samples", [])), maxlen=1440
        )
        if self._minute_samples:
            data_sorted = sorted(self._minute_samples)
            k = int(round((len(data_sorted) - 1) * self.config.base_percentile / 100))
            self.base_load = data_sorted[k]
