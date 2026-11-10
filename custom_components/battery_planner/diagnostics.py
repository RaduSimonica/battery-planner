"""What a bug report needs, in one download (Settings -> Devices & services -> three dots)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.system_info import async_get_system_info
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import PlannerConfigEntry
from .binary_sensor import free_windows


def _plan_summary(plan: Any, points: list[Any]) -> dict[str, Any]:
    return {
        "mode": plan.mode,
        "profitable": plan.profitable,
        "intervals": len(points),
        "first": points[0].start.isoformat() if points else None,
        "last_end": points[-1].end.isoformat() if points else None,
        "net_gain": round(plan.net_gain, 3),
        "earnings": round(plan.earnings, 3),
        "expenses": round(plan.expenses, 3),
        "degradation_cost": round(plan.degradation_cost, 3),
        "stored_value": round(plan.stored_value, 3),
        "energy_bought_kwh": round(plan.energy_bought_kwh, 3),
        "energy_sold_kwh": round(plan.energy_sold_kwh, 3),
        "energy_to_house_kwh": round(plan.energy_to_house_kwh, 3),
        "energy_from_solar_kwh": round(plan.energy_from_solar_kwh, 3),
        "curtailed_kwh": round(plan.curtailed_kwh, 3),
        "actions": dict(Counter(s.action for s in plan.slots)),
        "battery_kwh_min_max": [round(min(plan.soc), 2), round(max(plan.soc), 2)] if plan.soc else None,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: PlannerConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    integration = await async_get_integration(hass, entry.domain)
    info = await async_get_system_info(hass)
    series = data.series
    state = hass.states.get(coordinator.price_entity)
    return {
        "integration_version": str(integration.version),
        "home_assistant": {"version": info.get("version"), "timezone": str(dt_util.get_default_time_zone())},
        "now": dt_util.now().isoformat(),
        "config": dict(entry.data),
        "options": dict(entry.options),
        "mode": coordinator.mode,
        "price_source": {
            "entity": coordinator.price_entity,
            "state": state.state if state else None,
            "unit": data.unit,
            "intervals": len(list(series.window(dt_util.utc_from_timestamp(0), dt_util.utc_from_timestamp(4_000_000_000)))),
        },
        "plans": {
            str(offset): _plan_summary(plan, data.points.get(offset, [])) | {"assumptions": data.info.get(offset, {})}
            for offset, plan in data.plans.items()
        },
        "consumption": {
            "sensors": coordinator.load_sources.entities,
            "house_power_kw_now": coordinator.load_kw,
            "house_power_kw_smoothed": coordinator.load_avg_kw,
            "days_learned": coordinator.load.days_learned(),
            "hours_known": len(coordinator.load.hourly),
        },
        "solar": {
            "forecast_on_energy_dashboard": coordinator.has_solar_forecast,
            "production_sensors": list(coordinator.load_sources.solar_entities),
            "expected_kwh": {str(o): round(sum(v), 2) for o, v in data.solar.items()},
        },
        "free_energy_windows": len(free_windows(data, coordinator.battery)),
    }
