"""Reads the price source and the battery sensors, and keeps the plans current."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_utc_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import settings
from .arbitrage import BatteryParams, Plan, Slot, plan
from .const import CONF_BATTERY_CAPACITY_ENTITY, CONF_BATTERY_SOC_ENTITY, CONF_PRICE_ENTITY, DOMAIN
from .prices import PricePoint, PriceSeries, currency_of, local_day_bounds, parse_prices

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = ("unknown", "unavailable")


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

    async def async_start(self) -> None:
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, [self.price_entity], self._source_changed)
        )
        self.config_entry.async_on_unload(
            async_track_utc_time_change(self.hass, self._tick, minute=(0, 15, 30, 45), second=2)
        )
        await self.async_refresh()

    @callback
    def _source_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self.async_refresh())

    @callback
    def _tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_refresh())

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

    def battery_inputs(self) -> tuple[BatteryParams, float, str]:
        """Battery parameters and the energy it holds now, from the user's sensors.

        Falls back to the configured capacity and an empty battery when a sensor
        is not set or not available.
        """
        opts = self.config_entry.options
        params = self.battery
        capacity = self._read_number(opts.get(CONF_BATTERY_CAPACITY_ENTITY))
        if capacity and capacity[0] > 0:
            kwh = capacity[0] / 1000 if capacity[1] == "Wh" else capacity[0]
            params = replace(params, capacity_kwh=kwh)
        soc = self._read_number(opts.get(CONF_BATTERY_SOC_ENTITY))
        if not soc:
            return params, 0.0, "assumed empty"
        value, unit = soc
        if unit == "kWh":
            energy = value
        elif unit == "Wh":
            energy = value / 1000
        else:  # percent
            energy = min(max(value, 0.0), 100.0) / 100 * params.capacity_kwh
        return params, min(max(energy, 0.0), params.capacity_kwh), "sensor"

    # -- planning ----------------------------------------------------------------

    async def _async_update_data(self) -> PlanData:
        self._read_source()
        params, energy, source = self.battery_inputs()
        now = datetime.now(timezone.utc)
        plans, info, points = await self.hass.async_add_executor_job(
            self._build_plans, self._series, params, energy, source, now
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
        self, series: PriceSeries, params: BatteryParams, energy: float, source: str, now: datetime
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
            slots = [Slot(p.start, p.buy, p.sell) for p in points]
            if offset == 0:  # the interval in progress has only part of its time left
                first = points[0]
                left = (first.end - max(now, first.start)) / (first.end - first.start)
                slots[0] = replace(slots[0], fraction=left)
            hours = (points[0].end - points[0].start).total_seconds() / 3600
            start_energy = energy if offset == 0 else 0.0
            plans[offset] = plan(slots, hours, params, start_energy)
            covered[offset] = points
            info[offset] = {
                "capacity_kwh": round(params.capacity_kwh, 2),
                "starting_energy_kwh": round(start_energy, 2),
                "starting_energy": source if offset == 0 else "assumed empty",
            }
        return plans, info, covered
