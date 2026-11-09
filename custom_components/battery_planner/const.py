"""Constants for the Battery Planner integration."""

DOMAIN = "battery_planner"
NAME = "Battery Planner"

CONF_PRICE_ENTITY = "price_entity"  # a sensor that follows the price series contract

# Battery (options)
CONF_BATTERY_CAPACITY = "battery_capacity_kwh"
CONF_BATTERY_CHARGE_POWER = "battery_charge_power_kw"
CONF_BATTERY_DISCHARGE_POWER = "battery_discharge_power_kw"
CONF_BATTERY_EFFICIENCY = "battery_round_trip_efficiency_pct"
CONF_BATTERY_DEGRADATION = "battery_degradation_per_kwh"
CONF_BATTERY_USABLE = "battery_usable_pct"  # share of the battery the planner may use
CONF_TIMER_HORIZON = "timer_horizon_hours"  # how far ahead the countdown sensors look
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"  # optional: live state of charge
CONF_BATTERY_CAPACITY_ENTITY = "battery_capacity_entity"  # optional: live usable capacity

BATTERY_DEFAULTS = {
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_BATTERY_CHARGE_POWER: 5.0,
    CONF_BATTERY_DISCHARGE_POWER: 5.0,
    CONF_BATTERY_EFFICIENCY: 90.0,
    CONF_BATTERY_DEGRADATION: 0.15,
    CONF_BATTERY_USABLE: 100.0,
    CONF_TIMER_HORIZON: 24,
}
