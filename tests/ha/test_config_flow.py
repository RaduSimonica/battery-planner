"""Config and options flow."""

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.battery_planner.const import DOMAIN

from .conftest import NOW, SOURCE, publish, rows, setup

BATTERY = {
    "battery_capacity_kwh": 13.5,
    "battery_charge_power_kw": 0.3,
    "battery_discharge_power_kw": 1.2,
    "battery_round_trip_efficiency_pct": 85,
    "battery_degradation_per_kwh": 0.2,
}


async def test_user_flow(hass, freezer):
    freezer.move_to(NOW)
    publish(hass, rows())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_entity": SOURCE})
    assert result["step_id"] == "battery"
    assert result["description_placeholders"]["currency"] == "RON"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], BATTERY)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Battery Planner (RON)"
    assert result["data"] == {"price_entity": SOURCE}
    assert result["options"] == BATTERY  # empty optional sensors are simply not stored


async def test_battery_sensors_can_be_set_at_setup(hass):
    publish(hass, rows())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_entity": SOURCE})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], BATTERY | {"battery_soc_entity": "sensor.soc"}
    )
    assert result["options"]["battery_soc_entity"] == "sensor.soc"


@pytest.mark.parametrize("attrs", [None, {}, {"prices": []}, {"prices": "x"}, {"prices": [{"start": "bad"}]}])
async def test_source_without_prices_is_refused(hass, attrs):
    if attrs is not None:
        hass.states.async_set(SOURCE, "5", attrs)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_entity": SOURCE})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "no_prices"}


async def test_second_entry_is_refused(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows())
    await setup(hass, entry)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT


async def _open(hass, entry, freezer, step):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": step})


async def _finish(hass, flow):
    assert flow["type"] is FlowResultType.MENU
    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "finish"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_battery_page_saves_and_clears_sensors(hass, entry, freezer):
    hass.config_entries.async_update_entry(
        entry, options={"battery_soc_entity": "sensor.soc", "battery_capacity_entity": "sensor.cap"}
    )
    form = await _open(hass, entry, freezer, "battery")
    assert form["type"] is FlowResultType.FORM
    flow = await hass.config_entries.options.async_configure(form["flow_id"], BATTERY)  # pickers left empty
    await _finish(hass, flow)
    assert "battery_soc_entity" not in entry.options and "battery_capacity_entity" not in entry.options
    assert entry.options["battery_discharge_power_kw"] == 1.2
    assert entry.runtime_data.battery.max_discharge_kw == 1.2  # reloaded with the new settings


async def test_options_are_not_saved_until_finish(hass, entry, freezer):
    form = await _open(hass, entry, freezer, "battery")
    flow = await hass.config_entries.options.async_configure(form["flow_id"], BATTERY)
    assert flow["type"] is FlowResultType.MENU and not entry.options
    hass.config_entries.options.async_abort(flow["flow_id"])
    assert not entry.options


async def test_changing_the_price_source(hass, entry, freezer):
    form = await _open(hass, entry, freezer, "source")
    hass.states.async_set("sensor.other_prices", "1", {"unit": "EUR/kWh", "prices": rows(since="2026-11-09T12:00:00+02:00")})
    bad = await hass.config_entries.options.async_configure(form["flow_id"], {"price_entity": "sensor.empty"})
    assert bad["errors"] == {"base": "no_prices"}
    flow = await hass.config_entries.options.async_configure(bad["flow_id"], {"price_entity": "sensor.other_prices"})
    await _finish(hass, flow)
    assert entry.data["price_entity"] == "sensor.other_prices"
    assert entry.runtime_data.price_entity == "sensor.other_prices"
    assert hass.states.get("sensor.battery_planner_net_gain_tomorrow").state not in ("unknown", "unavailable")
