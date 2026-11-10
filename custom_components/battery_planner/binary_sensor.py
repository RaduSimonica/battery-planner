"""Binary sensors for automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PlanData, PlannerCoordinator
from .entity import PlannerEntity

NOTHING = 0.001  # a price this close to zero is zero
WORTH_NOTICING_KWH = 0.05  # less surplus than this in an interval is not a surplus

BUY_AT_ZERO = "buy price at or below zero"
SOLD_FOR_NOTHING = "solar surplus that would be exported for nothing"
CURTAILED = "solar surplus beyond what can be stored or exported"

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: PlannerCoordinator = entry.runtime_data
    async_add_entities(
        PlannerProfitable(coordinator, f"profitable_{day}", offset)
        for day, offset in (("today", 0), ("tomorrow", 1))
    )
    async_add_entities([FreeEnergy(coordinator)])


class PlannerProfitable(PlannerEntity, BinarySensorEntity):
    """On when the plan for the day makes a profit."""

    def __init__(self, coordinator: PlannerCoordinator, key: str, offset: int) -> None:
        super().__init__(coordinator, key)
        self._offset = offset

    @property
    def is_on(self) -> bool | None:
        plan = self.coordinator.data.plans.get(self._offset)
        return None if plan is None else plan.profitable


def free_windows(d: PlanData) -> list[dict[str, Any]]:
    """Stretches of the plan where energy costs nothing, or is wasted unless used.

    An interval counts when importing is free or paid (a buy price of zero or less), or
    when solar surplus is left over after the battery has taken what the plan stores,
    and it would be exported for nothing (a sell price of zero or less) or cannot be
    stored or exported at all (curtailed). Neighbouring intervals are merged.
    """
    rows: list[dict[str, Any]] = []
    for offset in (0, 1):
        plan, points = d.plans.get(offset), d.points.get(offset)
        if plan is None or not points:
            continue
        by_start = {s.start: s for s in plan.slots}
        loads = d.loads.get(offset) or [0.0] * len(points)
        sun = d.solar.get(offset) or [0.0] * len(points)
        flows = plan.flows if len(plan.flows) == len(points) else None
        for i, p in enumerate(points):
            step = by_start.get(p.start)
            stored = step.from_solar_kwh if step else 0.0
            left = max(max(sun[i] - loads[i], 0.0) - stored, 0.0)
            cut = flows[i].curtailed_kwh if flows else 0.0
            if p.buy <= NOTHING:
                reason, kwh = BUY_AT_ZERO, 0.0
            elif cut > WORTH_NOTICING_KWH:
                reason, kwh = CURTAILED, cut
            elif left > WORTH_NOTICING_KWH and p.sell <= NOTHING:
                reason, kwh = SOLD_FOR_NOTHING, left
            else:
                continue
            last = rows[-1] if rows else None
            if last and last["_end"] == p.start and last["reason"] == reason:
                last["_end"], last["kwh"] = p.end, last["kwh"] + kwh
            else:
                rows.append({"_start": p.start, "_end": p.end, "reason": reason, "kwh": kwh})
    return rows


class FreeEnergy(PlannerEntity, BinarySensorEntity):
    """On while energy is free: a good moment for heavy loads (heat pump, washer, EV).

    The planner only plans the battery, so this is a signal for your automations.
    """

    _unrecorded_attributes = frozenset({"windows"})

    def __init__(self, coordinator: PlannerCoordinator) -> None:
        super().__init__(coordinator, "free_energy")

    def _now_and_next(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        now = dt_util.utcnow()
        windows = free_windows(self.coordinator.data)
        current = next((w for w in windows if w["_start"] <= now < w["_end"]), None)
        upcoming = next((w for w in windows if w["_start"] > now), None)
        return current, upcoming

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data.plans:
            return None
        return self._now_and_next()[0] is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current, upcoming = self._now_and_next()
        local = lambda t: dt_util.as_local(t).isoformat()  # noqa: E731
        out: dict[str, Any] = {}
        if current:
            out |= {"reason": current["reason"], "until": local(current["_end"])}
        if upcoming:
            out |= {"next_start": local(upcoming["_start"]), "next_end": local(upcoming["_end"]),
                    "next_reason": upcoming["reason"]}
        out["windows"] = [
            {"from": local(w["_start"]), "to": local(w["_end"]), "reason": w["reason"], "surplus_kwh": round(w["kwh"], 2)}
            for w in free_windows(self.coordinator.data)
        ]
        return out
