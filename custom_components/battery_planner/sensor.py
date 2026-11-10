"""Sensors: planned gain, gain per kWh, and what the battery should do now."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.util import dt as dt_util

from . import settings
from .arbitrage import Plan
from .const import CONF_TIMER_HORIZON
from .coordinator import PlanData, PlannerCoordinator
from .entity import PlannerEntity

PARALLEL_UPDATES = 0

DAYS = {"today": 0, "tomorrow": 1}


def _local(moment: datetime) -> str:
    return dt_util.as_local(moment).isoformat()


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _blocks(plan: Plan, step: timedelta) -> list[dict[str, Any]]:
    """Merge consecutive plan slots of the same action into windows."""
    blocks: list[dict[str, Any]] = []
    for s in plan.slots:
        last = blocks[-1] if blocks else None
        if last and last["action"] == s.action and last["_end"] == s.start:
            last["_end"] = s.start + step
            last["kwh"] += s.grid_kwh + s.from_solar_kwh
            last["solar"] += s.from_solar_kwh
            last["_cost"] += s.grid_kwh * s.price
        else:
            blocks.append(
                {"action": s.action, "from": s.start, "_end": s.start + step,
                 "kwh": s.grid_kwh + s.from_solar_kwh, "solar": s.from_solar_kwh,
                 "_cost": s.grid_kwh * s.price}
            )
    return [
        {
            "action": b["action"],
            "from": _local(b["from"]),
            "to": _local(b["_end"]),
            "kwh": round(b["kwh"], 3),
            **({"from_solar_kwh": round(b["solar"], 3)} if b["action"] == "charge" else {}),
            "avg_price": round(b["_cost"] / b["kwh"], 4),
        }
        for b in blocks
    ]


@dataclass(frozen=True, kw_only=True)
class PlannerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PlanData], Any]
    attrs_fn: Callable[[PlanData], dict[str, Any]] | None = None
    unit_kind: str | None = None  # "price" | "money" | None


def _plan_attrs(offset: int) -> Callable[[PlanData], dict[str, Any]]:
    def attrs(d: PlanData) -> dict[str, Any]:
        plan = d.plans.get(offset)
        pts = d.points.get(offset)
        if plan is None or not pts:
            return {}
        out: dict[str, Any] = {"profitable": plan.profitable} | d.info.get(offset, {})
        if plan.profitable:
            out |= {
                "earnings": round(plan.earnings, 4),
                "expenses": round(plan.expenses, 4),
                "degradation_cost": round(plan.degradation_cost, 4),
                "stored_energy_value": round(plan.stored_value, 4),
                "energy_to_house_kwh": round(plan.energy_to_house_kwh, 2),
                "energy_from_solar_kwh": round(plan.energy_from_solar_kwh, 2),
                "solar_curtailed_kwh": round(plan.curtailed_kwh, 2),
                "energy_bought_kwh": round(plan.energy_bought_kwh, 2),
                "energy_sold_kwh": round(plan.energy_sold_kwh, 2),
                "gain_per_kwh_sold": _round(plan.gain_per_kwh_sold),
                "gain_per_kwh_bought": _round(plan.gain_per_kwh_bought),
                "full_cycles": round(plan.cycles, 2),
                "cheapest_charge_breaks_even_at": _round(plan.break_even_sell_price),
                "schedule": _blocks(plan, pts[0].end - pts[0].start),
            }
        return out

    return attrs


def _net_result(offset: int) -> Callable[[PlanData], float | None]:
    """Cash gain plus the change in the value of the energy left in the battery."""

    def value(d: PlanData) -> float | None:
        plan = d.plans.get(offset)
        return None if plan is None else _round(plan.net_gain + plan.stored_value)

    return value


def _plan_value(offset: int, field: str) -> Callable[[PlanData], float | None]:
    def value(d: PlanData) -> float | None:
        plan = d.plans.get(offset)
        return None if plan is None else _round(getattr(plan, field) or 0.0)

    return value


def _action(d: PlanData) -> str | None:
    plan = d.plans.get(0)
    if plan is None:
        return None
    now = d.series.at(datetime.now(timezone.utc))
    for slot in plan.slots:
        if now and slot.start == now.start:
            return slot.action
    return "idle"


def _action_attrs(d: PlanData) -> dict[str, Any]:
    plan = d.plans.get(0)
    if plan is None:
        return {}
    now = datetime.now(timezone.utc)
    upcoming = next((s for s in plan.slots if s.start > now and s.action != _action(d)), None)
    return {"next_change": _local(upcoming.start), "next_action": upcoming.action} if upcoming else {}


def _windows(offset: int, action: str) -> Callable[[PlanData], dict[str, Any]]:
    """The planned windows of one kind (charge or discharge) with their energy and price."""

    def attrs(d: PlanData) -> dict[str, Any]:
        plan, pts = d.plans.get(offset), d.points.get(offset)
        if plan is None or not pts:
            return {}
        step = pts[0].end - pts[0].start
        return {"windows": [b for b in _blocks(plan, step) if b["action"] == action]}

    return attrs


def _energy(offset: int, field: str) -> Callable[[PlanData], float | None]:
    def value(d: PlanData) -> float | None:
        plan = d.plans.get(offset)
        return None if plan is None else round(getattr(plan, field), 2)

    return value


def _curve(d: PlanData, offset: int) -> list[tuple[datetime, float]] | None:
    """(time, stored kWh of the whole battery) from the plan's start to its end."""
    plan, pts, info = d.plans.get(offset), d.points.get(offset), d.info.get(offset)
    if plan is None or not pts or not info or len(plan.soc) != len(pts) + 1:
        return None
    times = [p.start for p in pts] + [pts[-1].end]
    return list(zip(times, plan.soc))


def _soc_value(offset: int) -> Callable[[PlanData], float | None]:
    def value(d: PlanData) -> float | None:
        curve = _curve(d, offset)
        if not curve:
            return None
        total = d.info[offset]["capacity_kwh"] + d.info[offset]["reserve_kwh"]
        return round(curve[-1][1] / total * 100, 1) if total > 0 else None

    return value


def _soc_attrs(offset: int) -> Callable[[PlanData], dict[str, Any]]:
    def attrs(d: PlanData) -> dict[str, Any]:
        curve = _curve(d, offset)
        if not curve:
            return {}
        info = d.info[offset]
        total = info["capacity_kwh"] + info["reserve_kwh"]
        peak_at, peak = max(curve, key=lambda c: (c[1], -c[0].timestamp()))  # earliest peak
        return {
            "end_kwh": round(curve[-1][1], 2),
            "start_kwh": round(curve[0][1], 2),
            "battery_kwh": round(total, 2),
            "reserve_kwh": info["reserve_kwh"],
            "peak_kwh": round(peak, 2),
            "peak_at": _local(peak_at),
            "curve": [
                {"at": _local(t), "kwh": round(k, 3), "pct": round(k / total * 100, 1)}
                for t, k in curve
            ],
        }

    return attrs


def _descriptions() -> list[PlannerSensorDescription]:
    out: list[PlannerSensorDescription] = []
    for day, offset in DAYS.items():
        out.append(PlannerSensorDescription(
            key=f"gain_{day}", unit_kind="money", suggested_display_precision=2,
            value_fn=_plan_value(offset, "net_gain"), attrs_fn=_plan_attrs(offset)))
        out.append(PlannerSensorDescription(
            key=f"net_result_{day}", unit_kind="money", suggested_display_precision=2,
            value_fn=_net_result(offset)))
        out.append(PlannerSensorDescription(
            key=f"earnings_{day}", unit_kind="money", suggested_display_precision=2,
            value_fn=_plan_value(offset, "earnings")))
        out.append(PlannerSensorDescription(
            key=f"expenses_{day}", unit_kind="money", suggested_display_precision=2,
            value_fn=_plan_value(offset, "expenses")))
        out.append(PlannerSensorDescription(
            key=f"gain_per_kwh_{day}", unit_kind="price", suggested_display_precision=3,
            value_fn=_plan_value(offset, "gain_per_kwh_sold")))
    for day, offset in DAYS.items():
        out.append(PlannerSensorDescription(
            key=f"energy_to_charge_{day}", device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2,
            value_fn=_energy(offset, "energy_bought_kwh"), attrs_fn=_windows(offset, "charge")))
        out.append(PlannerSensorDescription(
            key=f"energy_to_export_{day}", device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2,
            value_fn=_energy(offset, "energy_sold_kwh"), attrs_fn=_windows(offset, "discharge")))
        out.append(PlannerSensorDescription(
            key=f"projected_soc_{day}", device_class=SensorDeviceClass.BATTERY,
            native_unit_of_measurement="%", suggested_display_precision=0,
            value_fn=_soc_value(offset), attrs_fn=_soc_attrs(offset)))
    out.append(PlannerSensorDescription(
        key="action", device_class=SensorDeviceClass.ENUM,
        options=["charge", "discharge", "idle"], value_fn=_action, attrs_fn=_action_attrs))
    return out


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: PlannerCoordinator = entry.runtime_data
    async_add_entities(PlannerSensor(coordinator, d) for d in _descriptions())
    async_add_entities(
        [CountdownSensor(coordinator, "time_to_cheapest_price", "buy", cheapest=True),
         CountdownSensor(coordinator, "time_to_best_sell_price", "sell", cheapest=False)]
    )
    async_add_entities([PlanTableSensor(coordinator)])
    if coordinator.has_solar_forecast:
        async_add_entities([ExpectedSolarSensor(coordinator, "expected_solar_today", 0),
                            ExpectedSolarSensor(coordinator, "expected_solar_tomorrow", 1)])
    if coordinator.tracks_load:
        async_add_entities(
            [HousePowerSensor(coordinator), ExpectedConsumptionSensor(coordinator)]
            + ([ExpectedConsumptionSensor(coordinator, with_ev=True)] if coordinator.load_sources.ev else [])
        )


class PlannerSensor(PlannerEntity, SensorEntity):
    entity_description: PlannerSensorDescription
    _unrecorded_attributes = frozenset({"schedule", "windows", "curve"})

    def __init__(self, coordinator: PlannerCoordinator, description: PlannerSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        kind = self.entity_description.unit_kind
        data = self.coordinator.data
        if kind == "price":
            return data.unit
        if kind == "money":
            return data.currency  # only a real currency code, never free text
        return self.entity_description.native_unit_of_measurement

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self.entity_description.unit_kind == "money" and self.coordinator.data.currency:
            return SensorDeviceClass.MONETARY
        return self.entity_description.device_class

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class CountdownSensor(PlannerEntity, SensorEntity):
    """Minutes until the cheapest buy price or the best sell price starts.

    The state changes every minute, so the entity keeps its own timer instead of
    making every sensor update that often.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _unrecorded_attributes = frozenset({"at", "ends_at", "price"})

    def __init__(self, coordinator: PlannerCoordinator, key: str, side: str, *, cheapest: bool) -> None:
        super().__init__(coordinator, key)
        self._side = side
        self._cheapest = cheapest

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_track_utc_time_change(self.hass, self._minute, second=1))

    @callback
    def _minute(self, _now: datetime) -> None:
        self.async_write_ha_state()

    def _best(self):
        now = dt_util.utcnow()
        hours = settings.get(self.coordinator.config_entry.options, CONF_TIMER_HORIZON)
        points = [
            p for p in self.coordinator.data.series.window(now, now + timedelta(hours=hours))
            if p.end > now
        ]
        if not points:
            return now, None
        price = lambda p: getattr(p, self._side)  # noqa: E731
        key = (lambda p: (price(p), p.start)) if self._cheapest else (lambda p: (-price(p), p.start))
        return now, min(points, key=key)

    @property
    def native_value(self) -> int | None:
        now, best = self._best()
        if best is None:
            return None
        return 0 if best.start <= now else ceil((best.start - now).total_seconds() / 60)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        _, best = self._best()
        if best is None:
            return None
        return {"at": _local(best.start), "ends_at": _local(best.end), "price": getattr(best, self._side)}


class HousePowerSensor(PlannerEntity, SensorEntity):
    """What the house uses right now, worked out from the picked sensors."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: PlannerCoordinator) -> None:
        super().__init__(coordinator, "house_power")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.add_load_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        kw = self.coordinator.load_kw
        return None if kw is None else round(kw, 3)


class ExpectedConsumptionSensor(PlannerEntity, SensorEntity):
    """Energy the house is expected to use over the next 24 hours, from its own history.

    The plain sensor leaves an EV charger out (it is a large, unpredictable load). With
    ``with_ev`` the charger is left in, as the average of what it has drawn at each hour.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(self, coordinator: PlannerCoordinator, *, with_ev: bool = False) -> None:
        super().__init__(coordinator, "expected_consumption_with_ev" if with_ev else "expected_consumption")
        self._with_ev = with_ev

    def _profile(self, with_ev: bool | None = None):
        use_ev = self._with_ev if with_ev is None else with_ev
        return self.coordinator.load_with_ev if use_ev else self.coordinator.load

    def _forecast(self, with_ev: bool | None = None) -> list[tuple[datetime, float | None]]:
        now = dt_util.utcnow()
        # From the start of the hour in progress, so the sum is a full 24 hours.
        return self._profile(with_ev).forecast(now, 24, now.timestamp(), dt_util.get_default_time_zone())

    @staticmethod
    def _total(forecast: list[tuple[datetime, float | None]]) -> float | None:
        known = [k for _, k in forecast if k is not None]
        # Fewer than 24 known hours would understate it.
        return round(sum(known), 2) if len(known) == 24 else None

    @property
    def native_value(self) -> float | None:
        return self._total(self._forecast())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        forecast = self._forecast()
        out: dict[str, Any] = {
            "days_learned": self._profile().days_learned(),
            "hours_known": sum(1 for _, k in forecast if k is not None),
            "forecast": [
                {"start": _local(t), "kwh": None if k is None else round(k, 3)} for t, k in forecast
            ],
        }
        if self._with_ev:
            house = self._total(self._forecast(False))
            total = self._total(forecast)
            out["ev_kwh"] = None if house is None or total is None else round(max(total - house, 0.0), 2)
            out["without_ev_kwh"] = house
        return out


class PlanTableSensor(PlannerEntity, SensorEntity):
    """The plan, interval by interval, for charts and cards (like the price source's list).

    The state is the number of intervals. The ``plan`` attribute has one row per
    interval from now to the end of tomorrow, as far as prices are known.
    """

    _attr_suggested_display_precision = 0
    _unrecorded_attributes = frozenset({"plan"})

    def __init__(self, coordinator: PlannerCoordinator) -> None:
        super().__init__(coordinator, "plan")

    def _rows(self) -> list[dict[str, Any]]:
        d = self.coordinator.data
        rows: list[dict[str, Any]] = []
        earned = spent = net = 0.0  # running totals continue from today into tomorrow
        for offset in (0, 1):
            plan, pts, info = d.plans.get(offset), d.points.get(offset), d.info.get(offset)
            if plan is None or not pts or not info or len(plan.soc) != len(pts) + 1:
                continue
            total = info["capacity_kwh"] + info["reserve_kwh"]
            by_start = {s.start: s for s in plan.slots}
            loads = d.loads.get(offset) or [0.0] * len(pts)
            sun = d.solar.get(offset) or [0.0] * len(pts)
            cars = d.ev.get(offset) or [0.0] * len(pts)
            flows = plan.flows if len(plan.flows) == len(pts) else None
            curves = (plan.earnings_curve, plan.expenses_curve, plan.net_curve)
            for i, p in enumerate(pts):
                step = by_start.get(p.start)
                charge = step.grid_kwh if step and step.action == "charge" else 0.0  # from the grid
                out = step.grid_kwh if step and step.action == "discharge" else 0.0
                house = step.to_house_kwh if step else 0.0
                row = {
                    "start": _local(p.start),
                    "end": _local(p.end),
                    "buy": _round(p.buy),
                    "sell": _round(p.sell),
                    "action": step.action if step else "idle",
                    "charge_kwh": round(charge, 3),
                    "discharge_kwh": round(out, 3),
                    "to_house_kwh": round(house, 3),
                    "export_kwh": round(out - house, 3),
                    "load_kwh": round(loads[i], 3),
                    "solar_kwh": round(sun[i], 3),
                    "ev_kwh": round(cars[i], 3),
                    "solar_to_battery_kwh": round(step.from_solar_kwh if step else 0.0, 3),
                    "grid_import_kwh": round(flows[i].grid_import_kwh, 3) if flows else None,
                    "grid_export_kwh": round(flows[i].grid_export_kwh, 3) if flows else None,
                    "curtailed_kwh": round(flows[i].curtailed_kwh, 3) if flows else None,
                    "soc_kwh": round(plan.soc[i + 1], 3),
                    "soc_pct": round(plan.soc[i + 1] / total * 100, 1) if total > 0 else None,
                }
                if all(len(c) == len(pts) + 1 for c in curves):
                    row |= {
                        "earnings": round(earned + curves[0][i + 1], 3),
                        "expenses": round(spent + curves[1][i + 1], 3),
                        "net": round(net + curves[2][i + 1], 3),
                    }
                rows.append(row)
            if plan.net_curve:
                earned += plan.earnings_curve[-1]
                spent += plan.expenses_curve[-1]
                net += plan.net_curve[-1]
        return rows

    @property
    def native_value(self) -> int:
        return len(self._rows())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        info = d.info.get(0) or {}
        plan = d.plans.get(0)
        total = (info.get("capacity_kwh", 0) + info.get("reserve_kwh", 0)) or None
        out: dict[str, Any] = {
            "mode": info.get("mode"),
            "currency": d.currency,
            "battery_kwh": total,
            "reserve_kwh": info.get("reserve_kwh"),
            "now_soc_kwh": round(plan.soc[0], 3) if plan and plan.soc else None,
            "now_soc_pct": round(plan.soc[0] / total * 100, 1) if plan and plan.soc and total else None,
        }
        out |= {k: v for k, v in info.items() if k in ("house_load_shift_kw", "mode_note")}
        if self.coordinator.ev_settings:
            out["ev_surplus_probability"] = (
                None if self.coordinator.ev_probability is None else round(self.coordinator.ev_probability, 2)
            )
            out["ev_hours_learned"] = self.coordinator.ev_samples
        out["plan"] = self._rows()
        return out


class ExpectedSolarSensor(PlannerEntity, SensorEntity):
    """What the panels are expected to make (kWh): the rest of today, or tomorrow."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: PlannerCoordinator, key: str, offset: int) -> None:
        super().__init__(coordinator, key)
        self._offset = offset

    @property
    def native_value(self) -> float | None:
        values = self.coordinator.data.solar.get(self._offset)
        return None if values is None else round(sum(values), 2)
