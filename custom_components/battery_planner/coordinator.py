"""Reads the price source and the battery sensors, and keeps the plans current."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from math import exp, sqrt
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import fixed, settings, solar
from .arbitrage import MAX_PROFIT, MODES, BatteryParams, Plan, Slot, plan
from .const import (
    CONF_BATTERY_CAPACITY_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_USABLE,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SOURCE,
    DOMAIN,
    SOURCE_ENTITY,
    SOURCE_FIXED,
)
from .consumption import KEEP_DAYS, POWER_UNITS, LoadProfile, hourly_kwh, house_power_kw
from .prices import PricePoint, PriceSeries, currency_of, local_day_bounds, parse_prices

_LOGGER = logging.getLogger(__name__)

# Energy units a capacity or state-of-charge sensor may use, as a factor to kWh.
_CAPACITY_UNITS: dict[str | None, float] = {None: 1.0, "kWh": 1.0, "Wh": 0.001}

UNAVAILABLE = ("unknown", "unavailable")

# Following what is really happening, between the 15-minute plans.
LOAD_SMOOTHING_S = 600  # the live house power is averaged over about ten minutes
LOAD_FADE_HOURS = 1.5  # an unexpected load matters for the next hour or two, then the pattern rules
DRIFT_LOAD_KW = 0.75  # the house uses this much more or less than the plan assumed
DRIFT_SOC_KWH = 1.0  # the battery holds this much more or less than the plan expected
DRIFT_PAUSE_S = 300  # at most one extra plan this often


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
    loads: dict[int, list[float]] = field(default_factory=dict)  # expected house kWh per interval
    solar: dict[int, list[float]] = field(default_factory=dict)  # expected solar kWh per interval

    @property
    def currency(self) -> str | None:
        return currency_of(self.unit)


class PlannerCoordinator(DataUpdateCoordinator[PlanData]):
    """Re-plans when the prices change, and every 15 minutes as time (and the battery) moves."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        # The prices come from a sensor, or from fixed prices set up here.
        self.price_entity: str | None = entry.data.get(CONF_PRICE_ENTITY)
        self.fixed: fixed.FixedPrices | None = (
            fixed.from_data(entry.data)
            if entry.data.get(CONF_PRICE_SOURCE, SOURCE_ENTITY) == SOURCE_FIXED
            else None
        )
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
        self.load_avg_kw: float | None = None  # the house's power, smoothed
        self._avg_at: float | None = None
        self._assumed_kw: float | None = None  # what the last plan assumed for the interval in progress
        self._assumed_solar_kw: float | None = None
        self.has_solar_forecast = False
        self._planned_at: float = 0.0

    async def async_set_mode(self, mode: str) -> None:
        """Plan for another mode, from now on."""
        if mode in MODES and mode != self.mode:
            self.mode = mode
            await self.async_refresh()

    @property
    def tracks_load(self) -> bool:
        return self.load_sources.configured

    async def async_start(self) -> None:
        self.has_solar_forecast = await solar.has_forecast(self.hass)
        if self.load_sources.solar_entities:  # a sunnier or cloudier moment than forecast
            self.config_entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, list(self.load_sources.solar_entities), self._solar_sensor_changed
                )
            )
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
        if self.price_entity and not self.fixed:
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
        else:
            self._check_drift()

    @callback
    def _solar_sensor_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._check_drift()

    def _solar_live_kw(self) -> float | None:
        """What all the panels make now (kW), None if there is no sensor or one is unusable."""
        entities = self.load_sources.solar_entities
        readings = [self._power_kw(e) for e in entities]
        if not entities or any(r is None for r in readings):
            return None
        return max(sum(readings), 0.0)

    @callback
    def _check_drift(self) -> None:
        """Plan again, ahead of the 15-minute step, when reality leaves the plan behind.

        Either the house uses clearly more or less than the plan assumed for now
        (an oven, a washing machine), or the battery holds clearly more or less than
        the plan expected. The new plan starts from what is really happening.
        """
        now = dt_util.utcnow().timestamp()
        plan0, points = (self.data.plans.get(0), self.data.points.get(0)) if self.data else (None, None)
        if plan0 is None or not points or now - self._planned_at < DRIFT_PAUSE_S:
            return
        reasons = []
        if self.load_avg_kw is not None and self._assumed_kw is not None:
            if abs(self.load_avg_kw - self._assumed_kw) >= DRIFT_LOAD_KW:
                reasons.append("house load")
        live_solar = self._solar_live_kw()
        if live_solar is not None and self._assumed_solar_kw is not None:
            if abs(live_solar - self._assumed_solar_kw) >= DRIFT_LOAD_KW:
                reasons.append("solar production")
        battery = self.battery_inputs()
        here = next((i for i, p in enumerate(points) if p.start.timestamp() <= now < p.end.timestamp()), None)
        if (
            battery.source == "sensor"
            and here is not None
            and len(plan0.soc) > here
            and abs(battery.energy_kwh - plan0.soc[here]) >= DRIFT_SOC_KWH
        ):
            reasons.append("battery level")
        if reasons:
            _LOGGER.debug("Planning again early: %s differs from the plan", " and ".join(reasons))
            self._planned_at = now
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
        if self.load_kw is not None:
            now = dt_util.utcnow().timestamp()
            if self.load_avg_kw is None or self._avg_at is None:
                self.load_avg_kw = self.load_kw
            else:
                weight = 1 - exp(-max(now - self._avg_at, 0.0) / LOAD_SMOOTHING_S)
                self.load_avg_kw += weight * (self.load_kw - self.load_avg_kw)
            self._avg_at = now
        for listener in list(self._load_listeners):
            listener()
        self._check_drift()

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
        await self.async_refresh()  # the plan counts the house from now on

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
        if self.fixed:
            tz = dt_util.get_default_time_zone()
            today = dt_util.now().date()
            start = local_day_bounds(today, tz)[0]
            end = local_day_bounds(today + timedelta(days=1), tz)[1]
            self._series = PriceSeries(fixed.points(self.fixed, start, end, tz))
            self._unit = f"{self.hass.config.currency}/kWh"
            return
        state = self.hass.states.get(self.price_entity or "")
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
        self._planned_at = now.timestamp()
        forecast = await solar.fetch_forecast(self.hass) if self.has_solar_forecast else []
        plans, info, points, loads, sun, assumed, assumed_sun = await self.hass.async_add_executor_job(
            self._build_plans,
            self._series, battery, now, self.mode, self.load, self.load_avg_kw, forecast, self._solar_live_kw(),
        )
        self._assumed_kw, self._assumed_solar_kw = assumed, assumed_sun
        return PlanData(self._series, self._unit, plans, info, points, loads, sun)

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
        live_kw: float | None = None,
        forecast: list[solar.Interval] | None = None,
        solar_live_kw: float | None = None,
    ) -> tuple[
        dict[int, Plan],
        dict[int, dict[str, Any]],
        dict[int, list[PricePoint]],
        dict[int, list[float]],
        dict[int, list[float]],
        float | None,
        float | None,
    ]:
        """Today: the intervals still ahead, from the real battery level.
        Tomorrow: a full day, from where today's plan leaves the battery.

        Also returns the expected house energy per interval, and the power the plan
        assumed for the interval in progress (what drift is measured against).
        """
        plans: dict[int, Plan] = {}
        info: dict[int, dict[str, Any]] = {}
        covered: dict[int, list[PricePoint]] = {}
        expected: dict[int, list[float]] = {}
        sun: dict[int, list[float]] = {}
        assumed: float | None = None
        assumed_sun: float | None = None
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
            # What the house is expected to use. The plan only counts it in the house modes,
            # but it is always worked out, for the charts. Today starts from what is
            # happening now, fading back to the usual pattern.
            loads, shift = self._loads(points, hours, load, now, tz, live_kw if offset == 0 else None)
            if mode != MAX_PROFIT and loads is None:
                used_mode, note = MAX_PROFIT, "no consumption forecast yet, planned for maximum profit"
            # What the panels are expected to make; today follows what they make now.
            production = solar.slot_kwh(points, forecast or [])
            if offset == 0 and forecast:
                production = solar.follow_live(production, points, hours, now, solar_live_kw)
            slots = [
                Slot(p.start, p.buy, p.sell, load_kwh=loads[i] if loads else 0.0, solar_kwh=production[i])
                for i, p in enumerate(points)
            ]
            if offset == 0:  # the interval in progress has only part of its time left
                first = points[0]
                left = (first.end - max(now, first.start)) / (first.end - first.start)
                slots[0] = replace(
                    slots[0], fraction=left, load_kwh=slots[0].load_kwh * left,
                    solar_kwh=slots[0].solar_kwh * left,
                )
                if loads:
                    assumed = loads[0] / hours
                if forecast:
                    assumed_sun = production[0] / hours
            # Energy left at the end is worth what it would cost to buy again: the cheapest
            # buy price in the next day and a half, through the battery's efficiency.
            ahead = series.window(points[0].start, points[0].start + timedelta(hours=36))
            cheapest = min((p.buy for p in ahead), default=0.0)
            terminal = max(cheapest, 0.0) / sqrt(min(max(battery.params.round_trip_efficiency, 0.01), 1.0))
            # Tomorrow starts where today's plan leaves the battery (the reserve if there is none).
            if offset == 0:
                start_energy, origin = battery.energy_kwh, battery.source
            elif 0 in plans and plans[0].soc:
                start_energy, origin = plans[0].soc[-1], "end of today's plan"
            else:
                start_energy, origin = battery.reserve_kwh, "assumed empty"
            plans[offset] = plan(slots, hours, battery.params, start_energy, used_mode, terminal)
            covered[offset] = points
            expected[offset] = loads or [0.0] * len(points)
            sun[offset] = production
            info[offset] = {
                "capacity_kwh": round(battery.usable_kwh, 2),
                "reserve_kwh": round(battery.reserve_kwh, 2),
                "starting_energy_kwh": round(start_energy, 2),
                "available_kwh": round(max(start_energy - battery.reserve_kwh, 0.0), 2),
                "starting_energy": origin,
                "mode": used_mode,
            }
            if note:
                info[offset]["mode_note"] = note
            if loads:
                info[offset]["expected_consumption_kwh"] = round(sum(loads), 2)
            if shift is not None:
                info[offset]["house_load_shift_kw"] = round(shift, 2)
            if forecast:
                info[offset]["expected_solar_kwh"] = round(sum(production), 2)
        return plans, info, covered, expected, sun, assumed, assumed_sun

    @staticmethod
    def _loads(
        points: list[PricePoint],
        hours: float,
        load: LoadProfile | None,
        now: datetime,
        tz: Any,
        live_kw: float | None = None,
    ) -> tuple[list[float] | None, float | None]:
        """Expected house energy (kWh) in each interval, or None without a forecast.

        The profile has an hourly value, which is spread over the intervals of that
        hour. Hours it does not know take the average of those it does. With the live
        house power, the difference between it and the pattern for the interval in
        progress is carried forward and fades away over a couple of hours: an oven
        that is on now will be on a little longer, a house that is empty stays quiet.
        Returns the energies and that difference in kW (None when not applied).
        """
        if not load:
            return None, None
        expected = [load.expected(p.start, now.timestamp(), tz) for p in points]
        known = [e for e in expected if e is not None]
        if not known:
            return None, None
        fill = sum(known) / len(known)
        energies = [(fill if e is None else e) * hours for e in expected]
        if live_kw is None:
            return energies, None
        shift = live_kw - energies[0] / hours
        for i, p in enumerate(points):
            ahead = max((p.start - now).total_seconds() / 3600, 0.0)
            energies[i] = max(energies[i] + shift * hours * exp(-ahead / LOAD_FADE_HOURS), 0.0)
        return energies, shift
