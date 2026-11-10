"""Fixed prices as the price source: setting them up, planning with them, switching back."""

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.battery_planner.const import DOMAIN

from .conftest import NOW, SOURCE, publish, rows, setup
from .test_config_flow import BATTERY
from .test_consumption import import_hours

FIXED = {"price_source": "fixed", "fixed_buy_price": 1.5, "fixed_sell_price": 0.3}
NIGHT = {"rate2_from": "22:00:00", "rate2_to": "06:00:00", "rate2_buy_price": 0.6, "rate2_sell_price": 0.2}
PLAN = "sensor.battery_planner_plan"


@pytest.fixture(autouse=True)
def romanian_leu(hass):
    hass.config.currency = "RON"


async def begin(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "source_fixed"})


# -- setting them up -----------------------------------------------------------


async def test_the_flow_creates_an_entry_with_fixed_prices(hass):
    form = await begin(hass)
    assert form["type"] is FlowResultType.FORM and form["step_id"] == "source_fixed"
    assert form["description_placeholders"]["currency"] == "RON"
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"fixed_buy_price": 1.5, "fixed_sell_price": 0.3, "rate2_days": "every"}
    )
    assert result["step_id"] == "battery"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], BATTERY)
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Battery Planner (RON)"
    assert result["data"] == {"price_source": "fixed", "fixed_buy_price": 1.5, "fixed_sell_price": 0.3}


async def test_a_second_rate_is_kept(hass):
    form = await begin(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"fixed_buy_price": 1.5, "fixed_sell_price": 0.3, "rate2_days": "weekends", **NIGHT}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], BATTERY)
    assert result["data"] == {**FIXED, **NIGHT, "rate2_days": "weekends"}


@pytest.mark.parametrize(
    "extra, error",
    [
        ({"rate2_from": "22:00:00"}, "rate2_incomplete"),
        ({"rate2_from": "22:00:00", "rate2_to": "06:00:00", "rate2_buy_price": 0.6}, "rate2_incomplete"),
        ({**NIGHT, "rate2_to": "22:00:00"}, "rate2_same_time"),
    ],
)
async def test_a_second_rate_must_be_complete_and_span_time(hass, extra, error):
    form = await begin(hass)
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"fixed_buy_price": 1.5, "fixed_sell_price": 0.3, **extra}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}


# -- planning with them ----------------------------------------------------------


def entry_with(hass, data, options=None):
    e = MockConfigEntry(domain=DOMAIN, data=data, options=options or {})
    e.add_to_hass(hass)
    return e


async def test_the_planner_gets_prices_for_today_and_tomorrow(hass, freezer):
    freezer.move_to(NOW)  # 12:00 local on 9 November
    entry = entry_with(hass, {**FIXED, **NIGHT}, {"battery_capacity_kwh": 10})
    await setup(hass, entry)
    plan = hass.states.get(PLAN).attributes["plan"]
    assert len(plan) == 48 + 96  # the rest of today, and tomorrow
    by_hour = {r["start"][11:13]: r for r in plan if r["start"].startswith("2026-11-10")}
    assert (by_hour["12"]["buy"], by_hour["12"]["sell"]) == (1.5, 0.3)
    assert (by_hour["23"]["buy"], by_hour["23"]["sell"]) == (0.6, 0.2)
    assert (by_hour["03"]["buy"], by_hour["03"]["sell"]) == (0.6, 0.2)
    assert hass.states.get("sensor.battery_planner_net_gain_per_kwh_today").attributes["unit_of_measurement"] == "RON/kWh"


async def test_flat_prices_leave_nothing_to_trade(hass, freezer):
    freezer.move_to(NOW)
    entry = entry_with(hass, FIXED, {"battery_capacity_kwh": 10, "battery_soc_entity": "sensor.soc"})
    hass.states.async_set("sensor.soc", "0", {"unit_of_measurement": "%"})  # an empty battery: nothing to sell
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.battery_planner_arbitrage_profitable_today").state == "off"
    assert hass.states.get("sensor.battery_planner_action").state == "idle"


async def test_a_night_rate_makes_it_worth_filling_the_battery_for_the_house(hass, freezer):
    freezer.move_to(NOW)
    import_hours(hass, "sensor.house", lambda _: 0.8, unit="kW")
    await async_wait_recording_done(hass)
    hass.states.async_set("sensor.house", "0.8", {"unit_of_measurement": "kW"})
    entry = entry_with(
        hass, {**FIXED, **NIGHT},
        {
            "battery_capacity_kwh": 10, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5,
            "battery_degradation_per_kwh": 0.1, "house_power_entity": "sensor.house",
        },
    )
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.battery_planner_planning_mode", "option": "self_consumption"}, blocking=True,
    )
    await hass.async_block_till_done()
    tomorrow = hass.states.get("sensor.battery_planner_net_gain_tomorrow").attributes
    assert tomorrow["profitable"] and tomorrow["energy_bought_kwh"] > 3.0  # bought at 0.6, avoids 1.5
    charges = [r["start"][11:13] for r in hass.states.get(PLAN).attributes["plan"] if r["charge_kwh"] > 0.02]
    assert charges and all(h in ("22", "23", "00", "01", "02", "03", "04", "05") for h in charges)


async def test_when_exports_earn_nothing_surplus_sun_is_free_energy(hass, freezer, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from custom_components.battery_planner import solar

    freezer.move_to(NOW)
    eet = timezone(timedelta(hours=2))
    wh = {datetime(2026, 11, 9, h, tzinfo=eet).isoformat(): 3000 for h in range(9, 17)}

    async def fetch(_hass):
        return solar.to_intervals("fake", wh)

    async def has(_hass):
        return True

    monkeypatch.setattr(solar, "fetch_forecast", fetch)
    monkeypatch.setattr(solar, "has_forecast", has)
    import_hours(hass, "sensor.house", lambda _: 0.8, unit="kW")
    await async_wait_recording_done(hass)
    hass.states.async_set("sensor.house", "0.8", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.soc", "100", {"unit_of_measurement": "%"})
    entry = entry_with(
        hass, {**FIXED, "fixed_sell_price": 0.0},  # no credit for what goes to the grid
        {"battery_capacity_kwh": 1, "battery_soc_entity": "sensor.soc", "house_power_entity": "sensor.house"},
    )
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.battery_planner_planning_mode", "option": "balanced"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.battery_planner_free_energy").state == "on"


async def test_diagnostics_say_where_the_prices_come_from(hass, freezer):
    from custom_components.battery_planner.diagnostics import async_get_config_entry_diagnostics

    freezer.move_to(NOW)
    entry = entry_with(hass, FIXED, {"battery_capacity_kwh": 10})
    await setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["price_source"]["type"] == "fixed" and diag["price_source"]["entity"] is None
    assert diag["price_source"]["unit"] == "RON/kWh" and diag["price_source"]["intervals"] >= 144


# -- switching -----------------------------------------------------------------


async def open_source(hass, entry, freezer, step):
    freezer.move_to(NOW)
    await setup(hass, entry)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "source"})
    assert flow["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": step})


async def finish(hass, flow):
    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "finish"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_switching_from_a_price_sensor_to_fixed_prices(hass, entry, freezer):
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    form = await open_source(hass, entry, freezer, "source_fixed")
    flow = await hass.config_entries.options.async_configure(
        form["flow_id"], {"fixed_buy_price": 1.5, "fixed_sell_price": 0.3, "rate2_days": "every"}
    )
    await finish(hass, flow)
    assert dict(entry.data) == {"price_source": "fixed", "fixed_buy_price": 1.5, "fixed_sell_price": 0.3}
    assert entry.runtime_data.fixed is not None and entry.runtime_data.price_entity is None  # the sensor is forgotten
    assert hass.states.get(PLAN).attributes["plan"][0]["buy"] == 1.5


async def test_switching_back_to_a_price_sensor(hass, freezer):
    entry = entry_with(hass, {**FIXED, **NIGHT}, {"battery_capacity_kwh": 10})
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    form = await open_source(hass, entry, freezer, "source_entity")
    flow = await hass.config_entries.options.async_configure(form["flow_id"], {"price_entity": SOURCE})
    await finish(hass, flow)
    assert dict(entry.data) == {"price_source": "entity", "price_entity": SOURCE}  # the fixed prices are gone
    assert entry.runtime_data.fixed is None
    assert hass.states.get(PLAN).attributes["plan"][0]["sell"] != 0.3


async def test_the_fixed_page_shows_the_current_prices(hass, freezer):
    entry = entry_with(hass, {**FIXED, **NIGHT}, {"battery_capacity_kwh": 10})
    form = await open_source(hass, entry, freezer, "source_fixed")
    suggested = {k.schema: k.description["suggested_value"] for k in form["data_schema"].schema if k.description}
    assert suggested["fixed_buy_price"] == 1.5 and suggested["rate2_from"] == "22:00:00"
