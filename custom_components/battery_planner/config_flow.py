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
)

from . import settings
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
    CONF_EV_POWER_ENTITY,
    CONF_GRID_EXPORT_ENTITY,
    CONF_GRID_IMPORT_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_GRID_POWER_FLIP,
    CONF_HOUSE_POWER_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    CONF_TIMER_HORIZON,
    CONSUMPTION_ENTITIES,
    DOMAIN,
)
from .prices import currency_of, parse_prices

_PRICE_SOURCE = EntitySelector(EntitySelectorConfig(domain="sensor"))
_SENSOR = EntitySelector(EntitySelectorConfig(domain=["sensor", "number", "input_number"]))
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
        vol.Optional(CONF_SOLAR_POWER_ENTITY): _SENSOR,
        vol.Optional(CONF_BATTERY_POWER_ENTITY): _SENSOR,
        vol.Optional(CONF_BATTERY_POWER_FLIP, default=False): BooleanSelector(),
        vol.Optional(CONF_EV_POWER_ENTITY): _SENSOR,
    }


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
        errors: dict[str, str] = {}
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_PRICE_ENTITY])
            if state is None or not parse_prices(state.attributes.get("prices")):
                errors["base"] = "no_prices"
            else:
                self._data = dict(user_input)
                return await self.async_step_battery()
        schema = vol.Schema({vol.Required(CONF_PRICE_ENTITY): _PRICE_SOURCE})
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = battery_schema()
        if user_input is not None:
            state = self.hass.states.get(self._data[CONF_PRICE_ENTITY])
            code = currency_of(state.attributes.get("unit")) if state else None
            return self.async_create_entry(
                title=f"Battery Planner ({code})" if code else "Battery Planner",
                data=self._data,
                options=_clear_emptied({}, user_input),
            )
        return self.async_show_form(
            step_id="battery",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(schema), _suggested(schema, {})),
            description_placeholders={"currency": self._currency()},
        )

    def _currency(self) -> str:
        state = self.hass.states.get(self._data.get(CONF_PRICE_ENTITY, ""))
        return (currency_of(state.attributes.get("unit")) if state else None) or "currency"

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
        errors: dict[str, str] = {}
        if user_input is not None:
            state = self.hass.states.get(user_input[CONF_PRICE_ENTITY])
            if state is None or not parse_prices(state.attributes.get("prices")):
                errors["base"] = "no_prices"
            else:
                self._data |= user_input
                return await self.async_step_init()
        schema = vol.Schema({vol.Required(CONF_PRICE_ENTITY): _PRICE_SOURCE})
        return self.async_show_form(
            step_id="source",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
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
                vol.Schema(schema), user_input or _suggested(schema, self._options)
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
        state = self.hass.states.get(self._data.get(CONF_PRICE_ENTITY, ""))
        code = (currency_of(state.attributes.get("unit")) if state else None) or "currency"
        return self.async_show_form(
            step_id="battery",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), _suggested(schema, self._options)
            ),
            description_placeholders={"currency": code},
        )
