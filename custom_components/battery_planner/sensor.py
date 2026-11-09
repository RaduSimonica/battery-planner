"""Sensors: planned gain, gain per kWh, and what the battery should do now."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .arbitrage import Plan
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
            last["kwh"] += s.grid_kwh
            last["_cost"] += s.grid_kwh * s.price
        else:
            blocks.append(
                {"action": s.action, "from": s.start, "_end": s.start + step,
                 "kwh": s.grid_kwh, "_cost": s.grid_kwh * s.price}
            )
    return [
        {
            "action": b["action"],
            "from": _local(b["from"]),
            "to": _local(b["_end"]),
            "kwh": round(b["kwh"], 3),
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


def _descriptions() -> list[PlannerSensorDescription]:
    out: list[PlannerSensorDescription] = []
    for day, offset in DAYS.items():
        out.append(PlannerSensorDescription(
            key=f"gain_{day}", unit_kind="money", suggested_display_precision=2,
            value_fn=_plan_value(offset, "net_gain"), attrs_fn=_plan_attrs(offset)))
        out.append(PlannerSensorDescription(
            key=f"gain_per_kwh_{day}", unit_kind="price", suggested_display_precision=3,
            value_fn=_plan_value(offset, "gain_per_kwh_sold")))
    out.append(PlannerSensorDescription(
        key="action", device_class=SensorDeviceClass.ENUM,
        options=["charge", "discharge", "idle"], value_fn=_action, attrs_fn=_action_attrs))
    return out


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: PlannerCoordinator = entry.runtime_data
    async_add_entities(PlannerSensor(coordinator, d) for d in _descriptions())


class PlannerSensor(PlannerEntity, SensorEntity):
    entity_description: PlannerSensorDescription
    _unrecorded_attributes = frozenset({"schedule"})

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
        return None

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
