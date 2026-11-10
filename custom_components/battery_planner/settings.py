"""Map config-entry data and options to the battery model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .arbitrage import BatteryParams
from .const import (
    BATTERY_DEFAULTS,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_DEGRADATION,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_EFFICIENCY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_FLIP,
    CONF_EV_BATTERY_KW,
    CONF_EV_MAX_KW,
    CONF_EV_POWER_ENTITY,
    CONF_EV_START_KW,
    CONF_EV_SURPLUS,
    CONF_GRID_EXPORT_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_FLIP,
    CONF_HOUSE_POWER_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
)
from .consumption import LoadSources


def get(options: Mapping[str, Any], key: str) -> Any:
    return options.get(key, BATTERY_DEFAULTS[key])


def battery(options: Mapping[str, Any]) -> BatteryParams:
    return BatteryParams(
        capacity_kwh=get(options, CONF_BATTERY_CAPACITY),
        max_charge_kw=get(options, CONF_BATTERY_CHARGE_POWER),
        max_discharge_kw=get(options, CONF_BATTERY_DISCHARGE_POWER),
        round_trip_efficiency=get(options, CONF_BATTERY_EFFICIENCY) / 100,
        degradation_per_kwh=get(options, CONF_BATTERY_DEGRADATION),
    )


def load_sources(options: Mapping[str, Any]) -> LoadSources:
    return LoadSources(
        house=options.get(CONF_HOUSE_POWER_ENTITY) or None,
        grid=options.get(CONF_GRID_POWER_ENTITY) or None,
        grid_flip=bool(options.get(CONF_GRID_POWER_FLIP)),
        grid_import=options.get(CONF_GRID_IMPORT_ENTITY) or None,
        grid_export=options.get(CONF_GRID_EXPORT_ENTITY) or None,
        solar=_entities(options.get(CONF_SOLAR_POWER_ENTITY)),
        battery=options.get(CONF_BATTERY_POWER_ENTITY) or None,
        battery_flip=bool(options.get(CONF_BATTERY_POWER_FLIP)),
        ev=options.get(CONF_EV_POWER_ENTITY) or None,
    )


def _entities(value: Any) -> tuple[str, ...] | None:
    """One entity (older settings) or a list of them, as a tuple."""
    if not value:
        return None
    return (value,) if isinstance(value, str) else tuple(e for e in value if e)


@dataclass(frozen=True, slots=True)
class EvSurplus:
    """An EV charger that starts above some solar surplus and follows it up to its maximum."""

    start_kw: float
    max_kw: float
    battery_kw: float  # what the battery still gets while the car charges


def ev_surplus(options: Mapping[str, Any]) -> EvSurplus | None:
    """The charger's settings, None without an EV sensor or when the option is off."""
    if not options.get(CONF_EV_POWER_ENTITY) or not get(options, CONF_EV_SURPLUS):
        return None
    return EvSurplus(
        start_kw=max(float(get(options, CONF_EV_START_KW)), 0.1),
        max_kw=max(float(get(options, CONF_EV_MAX_KW)), 0.1),
        battery_kw=max(float(get(options, CONF_EV_BATTERY_KW)), 0.0),
    )
