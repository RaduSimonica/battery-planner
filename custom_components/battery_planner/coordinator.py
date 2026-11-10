"""Reads the price source and the battery sensors, and keeps the plans current."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import settings
from .arbitrage import MAX_PROFIT, MODES, BatteryParams, Plan, Slot, plan
from .const import (
    CONF_BATTERY_CAPACITY_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_USABLE,
    CONF_PRICE_ENTITY,
    DOMAIN,
)
from .consumption import KEEP_DAYS, POWER_UNITS, LoadProfile, hourly_kwh, house_power_kw
from .prices import PricePoint, PriceSeries, currency_of, local_day_bounds, parse_prices

_LOGGER = logging.getLogger(__name__)

# Energy units a capacity or state-of-charge sensor may use, as a factor to kWh.
_CAPACITY_UNITS: dict[str | None, float] = {None: 1.0, "kWh": 1.0, "Wh": 0.001}

UNAVAILABLE = ("unknown", "unavailable")


@dataclass(frozen=True, slots=True)
class BatteryState:
    """What the planner may use, and what it must leave alone."""

    params: BatteryParams  # capacity_kwh is the whole battery, min_kwh the reserve
    energy_kwh: float  # stored now, in the whole battery (may be below the reserve)
    source: str  # "sensor" | "assumed empty"
    total_kwh: float  # the whole battery
    reserve_kwh: float  # never discharged

    @property
    def usable_kwh(self) -> float:
        return self.total_kwh - self.reserve_kwh

    @property
    def available_kwh(self) -> float:
        """What can be sold: the energy above the reserve."""
        return max(self.energy_kwh - self.reserve_kwh, 0.0)


@dataclass(slots=True)
class PlanData:
    series: PriceSeries = field(default_factory=PriceSeries)
    unit: str | None = None  # e.g. "RON/kWh", as the price source reports it
    plans: dict[int, Plan] = field(default_factory=dict)  # by day offset from the local today
    info: dict[int, dict[str, Any]] = field(default_factory=dict)  # what each plan assumed
    points: dict[int, list[PricePoint]] = field(default_factory=dict)  # what each plan covers

    @property
    def currency(self) -> str | None:
        return currency_of(self.unit)


class PlannerCoordinator(DataUpdateCoordinator[PlanData]):
    """Re-plans when the prices change, and every 15 minutes as time (and the battery) moves."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.price_entity: str = entry.data[CONF_PRICE_ENTITY]
        self.battery: BatteryParams = settings.battery(entry.options)
        self.data = PlanData()
        self._series = PriceSeries()
        self._unit: str | None = None
        self._warned_units: set[str] = set()
        # House consumption: only when the user picked sensors for it.
        self.load_sources = settings.load_sources(entry.options)
        self.load = LoadProfile()  # learned from the recorder's hourly statistics
        self.load_kw: float | None = None  # the house's power now
        self._load_listeners: list[Callable[[], None]] = []
        self.mode: str = MAX_PROFIT  # set by the mode select, which remembers it

    async def async_set_mode(self, mode: str) -> None:
        """Plan for another mode, from now on."""
        if mode in MODES and mode != self.mode:
            self.mode = mode
            await self.async_refresh()

    @property
    def tracks_load(self) -> bool:
        return self.load_sources.configured

    async def async_start(self) -> None:
        if self.tracks_load:
            self._sample()
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, self.load_sources.entities, self._load_sensor_changed
                )
            )
            # The statistics change once an hour; read them now and just after each hour.
            self.config_entry.async_on_unload(
                async_track_utc_time_change(self.hass, self._hourly, minute=5, second=30)
            )
            self.config_entry.async_create_background_task(
                self.hass, self._learn(), f"{DOMAIN} learn consumption"
            )
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, [self.price_entity], self._source_changed)
        )
        sensors = [
            e for e in (
                self.config_entry.options.get(CONF_BATTERY_SOC_ENTITY),
                self.config_entry.options.get(CONF_BATTERY_CAPACITY_ENTITY),
            ) if e
        ]
        if sensors:
            self.config_entry.async_on_unload(
                async_track_state_change_event(self.hass, sensors, self._battery_sensor_changed)
            )
        self.config_entry.async_on_unload(
            async_track_utc_time_change(self.hass, self._tick, minute=(0, 15, 30, 45), second=2)
        )
        await self.async_refresh()

    @callback
    def _source_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_refresh())

    @callback
    def _battery_sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        """Plan again when a battery sensor comes back (e.g. after a restart).

        Ordinary changes of the level wait for the 15-minute tick; only the move
        from "no value" to a value is worth an immediate plan.
        """
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state in UNAVAILABLE:
            return
        if old is None or old.state in UNAVAILABLE:
            self.hass.async_create_task(self.async_refresh())

    @callback
    def _tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_refresh())

    # -- house consumption ---------------------------------------------------------

    def add_load_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the house's power is read again."""
        self._load_listeners.append(listener)
        return lambda: self._load_listeners.remove(listener)

    def _power_kw(self, entity_id: str) -> float | None:
        reading = self._read_number(entity_id)
        if reading is None:
            return None
        value, unit = reading
        factor = POWER_UNITS.get(unit or "")
        if factor is None:
            self._warn_unit(entity_id, unit, "W or kW")
            return None
        return value * factor

    @callback
    def _sample(self) -> None:
        """Work out the house's power from the sensors, for the live sensor."""
        self.load_kw = house_power_kw(self.load_sources, self._power_kw)
        for listener in list(self._load_listeners):
            listener()

    @callback
    def _load_sensor_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._sample()

    @callback
    def _hourly(self, _now: datetime) -> None:
        self.config_entry.async_create_background_task(
            self.hass, self._learn(), f"{DOMAIN} learn consumption"
        )

    async def _learn(self) -> None:
        """Rebuild the consumption pattern from the recorder's hourly statistics."""
        try:
            means = await self._statistics()
        except Exception:  # noqa: BLE001 - a recorder hiccup must not stop the planner
            _LOGGER.debug("Could not read the consumption statistics", exc_info=True)
            return
        self.load = LoadProfile(hourly_kwh(self.load_sources, means))
        self.async_update_listeners()

    async def _statistics(self) -> dict[str, dict[int, float]]:
        """Hourly mean power (kW) of every consumption sensor, from long-term statistics."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period

        entities = self.load_sources.entities
        end = dt_util.utcnow()
        start = end - timedelta(days=KEEP_DAYS)
        recorder = get_instance(self.hass)
        metadata = await recorder.async_add_executor_job(
            lambda: get_metadata(self.hass, statistic_ids=set(entities))
        )
        rows = await recorder.async_add_executor_job(
            lambda: statistics_during_period(
                self.hass, start, end, set(entities), "hour", None, {"mean"}
            )
        )
        means: dict[str, dict[int, float]] = {}
        for entity in entities:
            meta = metadata.get(entity)
            factor = POWER_UNITS.get(meta[1]["unit_of_measurement"] or "") if meta else None
            if factor is None:
                self._warn_statistics(entity, meta[1]["unit_of_measurement"] if meta else None)
                continue
            means[entity] = {
                int(r["start"]): r["mean"] * factor
                for r in rows.get(entity, [])
                if r.get("mean") is not None
            }
        return means

    def _warn_statistics(self, entity_id: str, unit: str | None) -> None:
        if entity_id not in self._warned_units:
            self._warned_units.add(entity_id)
            if unit is None:
                _LOGGER.warning(
                    "%s has no long-term statistics, so its history cannot be learned from "
                    "(it needs state_class: measurement and a unit of W or kW)", entity_id,
                )
            else:
                _LOGGER.warning("%s has statistics in %s, which cannot be used (use W or kW)", entity_id, unit)

    # -- inputs ----------------------------------------------------------------

    def _read_source(self) -> None:
        """Latest prices from the source. An unavailable source keeps the last good ones."""
        state = self.hass.states.get(self.price_entity)
        if state is None:
            _LOGGER.debug("Price source %s does not exist (yet)", self.price_entity)
            return
        if state.state in UNAVAILABLE:
            return
        points = parse_prices(state.attributes.get("prices"))
        if points:
            self._series = PriceSeries(points)
            self._unit = state.attributes.get("unit")
        else:
            _LOGGER.debug("%s has no usable 'prices' attribute", self.price_entity)

    def _read_number(self, entity_id: str | None) -> tuple[float, str | None] | None:
        """Numeric state of a user-provided sensor, with its unit, if usable."""
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None  # unknown / unavailable
        if value != value or abs(value) == float("inf"):
            return None
        return value, state.attributes.get("unit_of_measurement")

    def battery_inputs(self) -> BatteryState:
        """Battery parameters and the energy it holds now, from the user's sensors.

        Falls back to the configured capacity and a battery holding only its
        reserve when a sensor is not set or not available. The reserve (the part
        of the battery outside the usable share) is never discharged.
        """
        opts = self.config_entry.options
        total = self.battery.capacity_kwh
        capacity = self._read_number(opts.get(CONF_BATTERY_CAPACITY_ENTITY))
        if capacity and capacity[0] > 0:
            factor = _CAPACITY_UNITS.get(capacity[1])
            if factor is None:
                self._warn_unit(opts[CONF_BATTERY_CAPACITY_ENTITY], capacity[1], "kWh or Wh")
            else:
                total = capacity[0] * factor
        share = min(max(settings.get(opts, CONF_BATTERY_USABLE) / 100, 0.01), 1.0)
        usable = total * share
        reserve = total - usable
        params = replace(self.battery, capacity_kwh=total, min_kwh=reserve)
        stored = self._stored_energy(total)
        source = "assumed empty" if stored is None else "sensor"
        return BatteryState(params, reserve if stored is None else stored, source, total, reserve)

    def _stored_energy(self, total_kwh: float) -> float | None:
        """Energy in the whole battery (kWh) from the charge sensor, if usable."""
        entity = self.config_entry.options.get(CONF_BATTERY_SOC_ENTITY)
        soc = self._read_number(entity)
        if not soc:
            return None
        value, unit = soc
        if unit in (None, "%"):
            stored = min(max(value, 0.0), 100.0) / 100 * total_kwh
        elif unit in _CAPACITY_UNITS:
            stored = value * _CAPACITY_UNITS[unit]
        else:
            self._warn_unit(entity, unit, "%, kWh or Wh")
            return None
        return min(max(stored, 0.0), total_kwh)

    def _warn_unit(self, entity_id: str, unit: str | None, accepted: str) -> None:
        """Say once per sensor that its unit cannot be used (e.g. Ah, which needs a voltage)."""
        if entity_id not in self._warned_units:
            self._warned_units.add(entity_id)
            _LOGGER.warning(
                "%s reports %s, which cannot be used here (use a sensor in %s); ignoring it",
                entity_id, unit, accepted,
            )

    # -- planning ----------------------------------------------------------------

    async def _async_update_data(self) -> PlanData:
        self._read_source()
        battery = self.battery_inputs()
        now = datetime.now(timezone.utc)
        plans, info, points = await self.hass.async_add_executor_job(
            self._build_plans, self._series, battery, now, self.mode, self.load
        )
        return PlanData(self._series, self._unit, plans, info, points)

    @staticmethod
    def _uniform(points: list[PricePoint]) -> list[PricePoint]:
        """Keep the intervals of the most common length (the planner needs equal steps)."""
        if not points:
            return points
        length = Counter(p.end - p.start for p in points).most_common(1)[0][0]
        return [p for p in points if p.end - p.start == length]

    def _build_plans(
        self,
        series: PriceSeries,
        battery: BatteryState,
        now: datetime,
        mode: str = MAX_PROFIT,
        load: LoadProfile | None = None,
    ) -> tuple[dict[int, Plan], dict[int, dict[str, Any]], dict[int, list[PricePoint]]]:
        """Today: the intervals still ahead, from the real battery level.
        Tomorrow: a full day, battery assumed empty (its level then is unknown).
        """
        plans: dict[int, Plan] = {}
        info: dict[int, dict[str, Any]] = {}
        covered: dict[int, list[PricePoint]] = {}
        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        for offset in (0, 1):
            start, end = local_day_bounds(today + timedelta(days=offset), tz)
            if offset == 0:
                start = max(start, now)  # past intervals are not part of the plan
            points = series.window(start, end)
            if not points or points[-1].end < end or points[0].start > start:
                continue  # the source does not cover this day (yet)
            points = self._uniform(points)
            hours = (points[0].end - points[0].start).total_seconds() / 3600
            used_mode, note = mode, None
            loads = self._loads(points, hours, load, now, tz) if mode != MAX_PROFIT else None
            if mode != MAX_PROFIT and loads is None:
                used_mode, note = MAX_PROFIT, "no consumption forecast yet, planned for maximum profit"
            slots = [
                Slot(p.start, p.buy, p.sell, load_kwh=loads[i] if loads else 0.0)
                for i, p in enumerate(points)
            ]
            if offset == 0:  # the interval in progress has only part of its time left
                first = points[0]
                left = (first.end - max(now, first.start)) / (first.end - first.start)
                slots[0] = replace(slots[0], fraction=left, load_kwh=slots[0].load_kwh * left)
            # Energy left at the end is worth what it would cost to buy again: the cheapest
            # buy price in the next day and a half, through the battery's efficiency.
            ahead = series.window(points[0].start, points[0].start + timedelta(hours=36))
            cheapest = min((p.buy for p in ahead), default=0.0)
            terminal = max(cheapest, 0.0) / sqrt(min(max(battery.params.round_trip_efficiency, 0.01), 1.0))
            # Tomorrow's level is unknown: assume nothing to sell (only the reserve is held).
            start_energy = battery.energy_kwh if offset == 0 else battery.reserve_kwh
            plans[offset] = plan(slots, hours, battery.params, start_energy, used_mode, terminal)
            covered[offset] = points
            info[offset] = {
                "capacity_kwh": round(battery.usable_kwh, 2),
                "reserve_kwh": round(battery.reserve_kwh, 2),
                "starting_energy_kwh": round(start_energy, 2),
                "available_kwh": round(max(start_energy - battery.reserve_kwh, 0.0), 2),
                "starting_energy": battery.source if offset == 0 else "assumed empty",
                "mode": used_mode,
            }
            if note:
                info[offset]["mode_note"] = note
            if loads:
                info[offset]["expected_consumption_kwh"] = round(sum(loads), 2)
        return plans, info, covered

    @staticmethod
    def _loads(
        points: list[PricePoint], hours: float, load: LoadProfile | None, now: datetime, tz: Any
    ) -> list[float] | None:
        """Expected house energy (kWh) in each interval, or None without a forecast.

        The profile has an hourly value, which is spread over the intervals of that
        hour. Hours it does not know take the average of those it does.
        """
        if not load:
            return None
        expected = [load.expected(p.start, now.timestamp(), tz) for p in points]
        known = [e for e in expected if e is not None]
        if not known:
            return None
        fill = sum(known) / len(known)
        return [(fill if e is None else e) * hours for e in expected]
