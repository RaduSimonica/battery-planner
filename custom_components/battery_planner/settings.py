"""Map config-entry data and options to the battery model."""

from __future__ import annotations

from typing import Any, Mapping

from .arbitrage import BatteryParams
from .const import (
    BATTERY_DEFAULTS,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_DEGRADATION,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_EFFICIENCY,
)


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
