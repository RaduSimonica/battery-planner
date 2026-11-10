"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TimeSelector,
)

from . import fixed, settings
from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CAPACITY_ENTITY,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_DEGRADATION,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_EFFICIENCY,
    CONF_BATTERY_POWER_ENTITY,
    CONF_BATTERY_POWER_FLIP,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_USABLE,
    CONF_EV_BATTERY_KW,
    CONF_EV_MAX_KW,
    CONF_EV_POWER_ENTITY,
    CONF_EV_START_KW,
    CONF_EV_SURPLUS,
    CONF_FIXED_BUY,
    CONF_FIXED_SELL,
    CONF_GRID_EXPORT_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_FLIP,
    CONF_HOUSE_POWER_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_PRICE_SOURCE,
    CONF_RATE2_BUY,
    CONF_RATE2_DAYS,
    CONF_RATE2_FROM,
    CONF_RATE2_SELL,
    CONF_RATE2_TO,
    CONF_SOLAR_POWER_ENTITY,
    CONF_TIMER_HORIZON,
    CONSUMPTION_ENTITIES,
    DOMAIN,
    FIXED_KEYS,
    SOURCE_ENTITY,
    SOURCE_FIXED,
)
from .prices import currency_of, parse_prices

_PRICE_SOURCE = EntitySelector(EntitySelectorConfig(domain="sensor"))
_SENSOR = EntitySelector(EntitySelectorConfig(domain=["sensor", "number", "input_number"]))
_SENSORS = EntitySelector(EntitySelectorConfig(domain=["sensor", "number", "input_number"], multiple=True))
OPTIONAL_ENTITIES = (CONF_BATTERY_SOC_ENTITY, CONF_BATTERY_CAPACITY_ENTITY)


def _num(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def battery_schema() -> dict:
    return {
        vol.Required(CONF_BATTERY_CAPACITY): _num(0.1, 10000, 0.1, "kWh"),
        vol.Required(CONF_BATTERY_CHARGE_POWER): _num(0.01, 10000, 0.01, "kW"),
        vol.Required(CONF_BATTERY_DISCHARGE_POWER): _num(0.01, 10000, 0.01, "kW"),
        vol.Required(CONF_BATTERY_EFFICIENCY): _num(1, 100, 0.5, "%"),
        vol.Required(CONF_BATTERY_DEGRADATION): _num(0, 100, 0.001),
        vol.Required(CONF_BATTERY_USABLE, default=100): _num(1, 100, 1, "%"),
        vol.Optional(CONF_BATTERY_SOC_ENTITY): _SENSOR,
        vol.Optional(CONF_BATTERY_CAPACITY_ENTITY): _SENSOR,
    }


def _suggested(schema: dict, values: dict[str, Any]) -> dict[str, Any]:
    return {k.schema: values.get(k.schema, settings.BATTERY_DEFAULTS.get(k.schema)) for k in schema}


def consumption_schema() -> dict:
    return {
        vol.Optional(CONF_HOUSE_POWER_ENTITY): _SENSOR,
        vol.Optional(CONF_GRID_POWER_ENTITY): _SENSOR,
        vol.Optional(CONF_GRID_POWER_FLIP, default=False): BooleanSelector(),
        vol.Optional(CONF_GRID_IMPORT_ENTITY): _SENSOR,
        vol.Optional(CONF_GRID_EXPORT_ENTITY): _SENSOR,
        vol.Optional(CONF_SOLAR_POWER_ENTITY): _SENSORS,
        vol.Optional(CONF_BATTERY_POWER_ENTITY): _SENSOR,
        vol.Optional(CONF_BATTERY_POWER_FLIP, default=False): BooleanSelector(),
        vol.Optional(CONF_EV_POWER_ENTITY): _SENSOR,
        vol.Optional(CONF_EV_SURPLUS, default=True): BooleanSelector(),
        vol.Optional(CONF_EV_START_KW, default=4.6): _num(0.1, 50, 0.1, "kW"),
        vol.Optional(CONF_EV_MAX_KW, default=11): _num(0.1, 50, 0.1, "kW"),
        vol.Optional(CONF_EV_BATTERY_KW, default=1): _num(0, 50, 0.1, "kW"),
    }


def _solar_as_list(options: dict[str, Any]) -> dict[str, Any]:
    """Older settings kept one production sensor as text; the page now takes a list."""
    value = options.get(CONF_SOLAR_POWER_ENTITY)
    return options | {CONF_SOLAR_POWER_ENTITY: [value]} if isinstance(value, str) else options


def consumption_errors(values: dict[str, Any]) -> dict[str, str]:
    """What cannot work: a mix of grid sensors, or half a pair. Nothing at all is fine."""
    grid, imp, exp = (values.get(k) for k in (CONF_GRID_POWER_ENTITY, CONF_GRID_IMPORT_ENTITY, CONF_GRID_EXPORT_ENTITY))
    if grid and (imp or exp):
        return {"base": "grid_both"}
    if bool(imp) != bool(exp):
        return {"base": "grid_pair"}
    if not values.get(CONF_HOUSE_POWER_ENTITY) and not grid and not imp and any(
        values.get(k) for k in (CONF_SOLAR_POWER_ENTITY, CONF_BATTERY_POWER_ENTITY, CONF_EV_POWER_ENTITY)
    ):
        return {"base": "grid_missing"}
    return {}


def _price() -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=0, max=100, step="any", mode=NumberSelectorMode.BOX))


def fixed_schema() -> dict:
    return {
        vol.Required(CONF_FIXED_BUY): _price(),
        vol.Required(CONF_FIXED_SELL): _price(),
        vol.Optional(CONF_RATE2_FROM): TimeSelector(),
        vol.Optional(CONF_RATE2_TO): TimeSelector(),
        vol.Optional(CONF_RATE2_BUY): _price(),
        vol.Optional(CONF_RATE2_SELL): _price(),
        vol.Optional(CONF_RATE2_DAYS, default=fixed.EVERY_DAY): SelectSelector(
            SelectSelectorConfig(options=list(fixed.DAY_CHOICES), translation_key="rate2_days")
        ),
    }


def fixed_errors(values: dict[str, Any]) -> dict[str, str]:
    """The second rate needs all of its fields, and a start that differs from its end."""
    rate2 = (CONF_RATE2_FROM, CONF_RATE2_TO, CONF_RATE2_BUY, CONF_RATE2_SELL)
    given = [values.get(k) not in (None, "") for k in rate2]
    if any(given) and not all(given):
        return {"base": "rate2_incomplete"}
    if all(given) and values[CONF_RATE2_FROM] == values[CONF_RATE2_TO]:
        return {"base": "rate2_same_time"}
    return {}


def fixed_data(values: dict[str, Any]) -> dict[str, Any]:
    """The data stored for fixed prices (an emptied second rate is simply left out)."""
    keep = {k: v for k, v in values.items() if k in FIXED_KEYS and v not in (None, "")}
    if not all(k in keep for k in (CONF_RATE2_FROM, CONF_RATE2_TO, CONF_RATE2_BUY, CONF_RATE2_SELL)):
        for k in (CONF_RATE2_FROM, CONF_RATE2_TO, CONF_RATE2_BUY, CONF_RATE2_SELL, CONF_RATE2_DAYS):
            keep.pop(k, None)
    return {CONF_PRICE_SOURCE: SOURCE_FIXED} | keep


def source_currency(hass, data: dict[str, Any]) -> str:
    """The currency of the prices: the sensor's, or Home Assistant's for fixed prices."""
    if data.get(CONF_PRICE_SOURCE) == SOURCE_FIXED:
        return hass.config.currency or "currency"
    state = hass.states.get(data.get(CONF_PRICE_ENTITY, ""))
    return (currency_of(state.attributes.get("unit")) if state else None) or "currency"


def _clear_emptied(
    values: dict[str, Any], submitted: dict[str, Any], keys: tuple[str, ...] = OPTIONAL_ENTITIES
) -> dict[str, Any]:
    """The frontend leaves an emptied optional field out: that means 'cleared'."""
    values = values | submitted
    for key in keys:
        if key not in submitted:
            values.pop(key, None)
    return values


class BatteryPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["source_entity", "source_fixed"])

    async def async_step_source_entity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_PRICE_ENTITY])
            if state is None or not parse_prices(state.attributes.get("prices")):
                errors["base"] = "no_prices"
            else:
                self._data = {CONF_PRICE_SOURCE: SOURCE_ENTITY} | dict(user_input)
                return await self.async_step_battery()
        schema = vol.Schema({vol.Required(CONF_PRICE_ENTITY): _PRICE_SOURCE})
        return self.async_show_form(
            step_id="source_entity",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )

    async def async_step_source_fixed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        schema = fixed_schema()
        if user_input is not None:
            errors = fixed_errors(user_input)
            if not errors:
                self._data = fixed_data(user_input)
                return await self.async_step_battery()
        return self.async_show_form(
            step_id="source_fixed",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), user_input or {}),
            description_placeholders={"currency": self.hass.config.currency or "currency"},
            errors=errors,
        )

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = battery_schema()
        if user_input is not None:
            code = source_currency(self.hass, self._data)
            return self.async_create_entry(
                title=f"Battery Planner ({code})" if code != "currency" else "Battery Planner",
                data=self._data,
                options=_clear_emptied({}, user_input),
            )
        return self.async_show_form(
            step_id="battery",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), _suggested(schema, {})),
            description_placeholders={"currency": self._currency()},
        )

    def _currency(self) -> str:
        return source_currency(self.hass, self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return BatteryPlannerOptionsFlow()


class BatteryPlannerOptionsFlow(OptionsFlow):
    """Menu of settings pages. Nothing is saved until 'Save and close'."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._data: dict[str, Any] = {}
        self._loaded = False

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self._loaded:  # the options can be empty, so they cannot tell
            self._options = dict(self.config_entry.options)
            self._data = dict(self.config_entry.data)
            self._loaded = True
        return self.async_show_menu(step_id="init", menu_options=["source", "battery", "consumption", "general", "finish"])

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._data != dict(self.config_entry.data):
            self.hass.config_entries.async_update_entry(self.config_entry, data=self._data)
        return self.async_create_entry(data=self._options)

    async def async_step_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="source", menu_options=["source_entity", "source_fixed"])

    async def async_step_source_entity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_PRICE_ENTITY])
            if state is None or not parse_prices(state.attributes.get("prices")):
                errors["base"] = "no_prices"
            else:
                self._data = {CONF_PRICE_SOURCE: SOURCE_ENTITY} | dict(user_input)
                return await self.async_step_init()
        schema = vol.Schema({vol.Required(CONF_PRICE_ENTITY): _PRICE_SOURCE})
        return self.async_show_form(
            step_id="source_entity",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )

    async def async_step_source_fixed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        schema = fixed_schema()
        if user_input is not None:
            errors = fixed_errors(user_input)
            if not errors:
                self._data = fixed_data(user_input)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="source_fixed",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), user_input or self._data),
            description_placeholders={"currency": self.hass.config.currency or "currency"},
            errors=errors,
        )

    async def async_step_consumption(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = consumption_schema()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = consumption_errors(user_input)
            if not errors:
                self._options = _clear_emptied(self._options, user_input, CONSUMPTION_ENTITIES)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="consumption",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input or _suggested(schema, _solar_as_list(self._options))
            ),
            errors=errors,
        )

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = {vol.Required(CONF_TIMER_HORIZON): _num(1, 48, 1, "h")}
        if user_input is not None:
            self._options |= {CONF_TIMER_HORIZON: int(user_input[CONF_TIMER_HORIZON])}
            return await self.async_step_init()
        return self.async_show_form(
            step_id="general",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), _suggested(schema, self._options)
            ),
        )

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = battery_schema()
        if user_input is not None:
            self._options = _clear_emptied(self._options, user_input)
            return await self.async_step_init()
        code = source_currency(self.hass, self._data)
        return self.async_show_form(
            step_id="battery",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), _suggested(schema, self._options)
            ),
            description_placeholders={"currency": code},
        )
