"""The planning mode select, house load in the plan, and the earnings and expenses sensors."""

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .conftest import NOW, publish, rows, setup
from .test_consumption import import_hours

MODE = "select.battery_planner_planning_mode"
GAIN = "sensor.battery_planner_net_gain_tomorrow"
EXPORT = "sensor.battery_planner_energy_to_export_tomorrow"
EARN = "sensor.battery_planner_earnings_tomorrow"
SPEND = "sensor.battery_planner_expenses_tomorrow"
HOUSE = {"house_power_entity": "sensor.house"}
BATTERY = {"battery_capacity_kwh": 10, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5, "battery_degradation_per_kwh": 0.1}


async def start(hass, entry, freezer, options=None, learn=True):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    if learn:
        import_hours(hass, "sensor.house", lambda _: 0.8, unit="kW")  # a steady 0.8 kW
        await async_wait_recording_done(hass)
        hass.states.async_set("sensor.house", "0.8", {"unit_of_measurement": "kW"})
    hass.config_entries.async_update_entry(entry, options={**BATTERY, **(options or {})})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()


async def choose(hass, option):
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": option}, blocking=True)
    await hass.async_block_till_done()


async def test_the_mode_select_starts_at_max_profit(hass, entry, freezer):
    await start(hass, entry, freezer, HOUSE)
    state = hass.states.get(MODE)
    assert state.state == "max_profit"
    assert state.attributes["options"] == ["max_profit", "self_consumption", "balanced"]
    assert hass.states.get(GAIN).attributes["mode"] == "max_profit"


async def test_max_profit_ignores_the_house(hass, entry, freezer):
    await start(hass, entry, freezer, HOUSE)
    attrs = hass.states.get(GAIN).attributes
    assert attrs["energy_to_house_kwh"] == 0 and "expected_consumption_kwh" not in attrs
    assert float(hass.states.get(EXPORT).state) > 0  # it sells


async def test_self_consumption_covers_the_house_and_exports_nothing(hass, entry, freezer):
    await start(hass, entry, freezer, HOUSE)
    await choose(hass, "self_consumption")
    attrs = hass.states.get(GAIN).attributes
    assert attrs["mode"] == "self_consumption" and attrs["profitable"]
    assert attrs["expected_consumption_kwh"] == pytest.approx(0.8 * 24, abs=0.1)
    assert attrs["energy_to_house_kwh"] > 0
    assert float(hass.states.get(EXPORT).state) == pytest.approx(0.0, abs=1e-6)


async def test_balanced_covers_the_house_then_exports(hass, entry, freezer):
    await start(hass, entry, freezer, HOUSE)
    await choose(hass, "balanced")
    attrs = hass.states.get(GAIN).attributes
    assert attrs["mode"] == "balanced" and attrs["energy_to_house_kwh"] > 0
    assert float(hass.states.get(EXPORT).state) > 0


async def test_earnings_expenses_and_net_gain_agree(hass, entry, freezer):
    await start(hass, entry, freezer, HOUSE)
    for option in ("max_profit", "self_consumption", "balanced"):
        await choose(hass, option)
        attrs = hass.states.get(GAIN).attributes
        gain, earn, spend = (float(hass.states.get(e).state) for e in (GAIN, EARN, SPEND))
        assert earn > 0 and spend > 0
        assert gain == pytest.approx(earn - spend - attrs["degradation_cost"], abs=0.01)


async def test_without_a_forecast_the_plan_falls_back_and_says_so(hass, entry, freezer):
    await start(hass, entry, freezer, learn=False)  # no consumption sensors at all
    await choose(hass, "balanced")
    attrs = hass.states.get(GAIN).attributes
    assert attrs["mode"] == "max_profit"
    assert "no consumption forecast" in attrs["mode_note"]
    assert hass.states.get(MODE).state == "balanced"  # the choice is kept


async def test_the_mode_survives_a_reload(hass, entry, freezer):
    await start(hass, entry, freezer, HOUSE)
    await choose(hass, "self_consumption")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MODE).state == "self_consumption"
