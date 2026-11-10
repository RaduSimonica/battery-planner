"""Constants for the Battery Planner integration."""

DOMAIN = "battery_planner"
NAME = "Battery Planner"

CONF_PRICE_ENTITY = "price_entity"  # a sensor that follows the price series contract
CONF_PRICE_SOURCE = "price_source"  # "entity" (a price sensor, the default) or "fixed"
SOURCE_ENTITY = "entity"
SOURCE_FIXED = "fixed"

# Fixed prices (part of the entry's data): a buy and a sell price per kWh, taxes and VAT
# included, and an optional second rate for some hours (a night rate, for example).
CONF_FIXED_BUY = "fixed_buy_price"
CONF_FIXED_SELL = "fixed_sell_price"
CONF_RATE2_FROM = "rate2_from"
CONF_RATE2_TO = "rate2_to"
CONF_RATE2_BUY = "rate2_buy_price"
CONF_RATE2_SELL = "rate2_sell_price"
CONF_RATE2_DAYS = "rate2_days"
FIXED_KEYS = (
    CONF_FIXED_BUY, CONF_FIXED_SELL, CONF_RATE2_FROM, CONF_RATE2_TO,
    CONF_RATE2_BUY, CONF_RATE2_SELL, CONF_RATE2_DAYS,
)

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

# House consumption (options). All optional; together they tell the house's power.
CONF_HOUSE_POWER_ENTITY = "house_power_entity"  # measures the house directly
CONF_GRID_POWER_ENTITY = "grid_power_entity"  # one signed grid sensor
CONF_GRID_POWER_FLIP = "grid_power_flip"  # it reports export as positive
CONF_GRID_IMPORT_ENTITY = "grid_import_entity"  # or two sensors, one per direction
CONF_GRID_EXPORT_ENTITY = "grid_export_entity"
CONF_SOLAR_POWER_ENTITY = "solar_power_entity"
CONF_BATTERY_POWER_ENTITY = "battery_power_entity"  # signed, positive = charging
CONF_BATTERY_POWER_FLIP = "battery_power_flip"
CONF_EV_POWER_ENTITY = "ev_power_entity"  # optional EV charger, left out of the house's power

# An EV charger that follows the solar surplus (options, with the EV sensor).
CONF_EV_SURPLUS = "ev_takes_surplus"
CONF_EV_START_KW = "ev_start_surplus_kw"
CONF_EV_MAX_KW = "ev_max_power_kw"
CONF_EV_BATTERY_KW = "ev_battery_trickle_kw"

CONSUMPTION_ENTITIES = (
    CONF_HOUSE_POWER_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_EXPORT_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_EV_POWER_ENTITY,
)

BATTERY_DEFAULTS = {
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_BATTERY_CHARGE_POWER: 5.0,
    CONF_BATTERY_DISCHARGE_POWER: 5.0,
    CONF_BATTERY_EFFICIENCY: 90.0,
    CONF_BATTERY_DEGRADATION: 0.15,
    CONF_BATTERY_USABLE: 100.0,
    CONF_TIMER_HORIZON: 24,
    CONF_EV_SURPLUS: True,
    CONF_EV_START_KW: 4.6,
    CONF_EV_MAX_KW: 11.0,
    CONF_EV_BATTERY_KW: 1.0,
}
