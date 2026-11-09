"""The integration against a real Home Assistant test instance."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import NOW, SOURCE, publish, rows, setup

GAIN = "sensor.battery_planner_net_gain_today"
GAIN_TOMORROW = "sensor.battery_planner_net_gain_tomorrow"
ACTION = "sensor.battery_planner_action"


async def test_plans_today_and_tomorrow(hass: HomeAssistant, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)

    today = hass.states.get(GAIN)
    assert float(today.state) > 0 and today.attributes["unit_of_measurement"] == "RON"
    assert today.attributes["device_class"] == "monetary"
    # Only the evening is left today: nothing cheap to charge from, empty battery -> no cycle.
    assert today.attributes["starting_energy"] == "assumed empty"
    tomorrow = hass.states.get(GAIN_TOMORROW)
    assert float(tomorrow.state) > 0
    sched = tomorrow.attributes["schedule"]
    assert [b["action"] for b in sched] == ["charge", "discharge"]
    assert "2026-11-10T02:00" <= sched[0]["from"] < "2026-11-10T05:00"  # in the cheap hours
    assert "2026-11-10T18:00" <= sched[1]["from"] < "2026-11-10T21:00"  # in the dear hours
    assert hass.states.get("binary_sensor.battery_planner_arbitrage_profitable_tomorrow").state == "on"
    per_kwh = hass.states.get("sensor.battery_planner_net_gain_per_kwh_tomorrow")
    assert per_kwh.attributes["unit_of_measurement"] == "RON/kWh"


async def test_today_plan_with_a_full_battery_sells_the_evening(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.soc", "100", {"unit_of_measurement": "%"})
    hass.config_entries.async_update_entry(entry, options={"battery_soc_entity": "sensor.soc"})
    await setup(hass, entry)
    today = hass.states.get(GAIN)
    assert float(today.state) > 0
    assert today.attributes["starting_energy"] == "sensor" and today.attributes["starting_energy_kwh"] == 10.0
    assert [b["action"] for b in today.attributes["schedule"]] == ["discharge"]
    assert hass.states.get(ACTION).state == "idle"  # 12:00: the evening is hours away
    assert hass.states.get(ACTION).attributes["next_action"] == "discharge"


async def test_action_follows_the_clock(hass, entry, freezer):
    freezer.move_to("2026-11-09 18:00:30+02:00")
    prices = rows(since="2026-11-09T18:00:00+02:00")
    for row in prices[:4]:  # the first hour of the evening pays best
        row["sell"] = 3.0
    publish(hass, prices)
    hass.states.async_set("sensor.soc", "100", {"unit_of_measurement": "%"})
    hass.config_entries.async_update_entry(entry, options={"battery_soc_entity": "sensor.soc"})
    await setup(hass, entry)
    assert hass.states.get(ACTION).state == "discharge"
    freezer.move_to("2026-11-09 22:00:03+02:00")
    hass.states.async_set("sensor.soc", "0", {"unit_of_measurement": "%"})  # sold during the evening
    publish(hass, rows(since="2026-11-09T22:00:00+02:00"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ACTION).state == "idle"


async def test_replans_when_the_prices_change(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.battery_planner_arbitrage_profitable_tomorrow").state == "on"
    publish(hass, rows(since="2026-11-09T12:00:00+02:00", low=0.9, high=0.9, mid=0.9))  # flat
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.battery_planner_arbitrage_profitable_tomorrow").state == "off"
    assert float(hass.states.get(GAIN_TOMORROW).state) == 0


async def test_tomorrow_unknown_until_published(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(days=1, since="2026-11-09T12:00:00+02:00"))  # only today
    await setup(hass, entry)
    assert hass.states.get(GAIN_TOMORROW).state == "unknown"
    assert hass.states.get("binary_sensor.battery_planner_arbitrage_profitable_tomorrow").state == "unknown"
    assert hass.states.get(GAIN).state != "unknown"
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))  # tomorrow arrives
    await hass.async_block_till_done()
    assert float(hass.states.get(GAIN_TOMORROW).state) > 0


async def test_source_that_does_not_exist_yet(hass, entry, freezer):
    """HA starts the planner before the price integration: no error, picked up later."""
    freezer.move_to(NOW)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(GAIN).state == "unknown"
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await hass.async_block_till_done()
    assert hass.states.get(GAIN_TOMORROW).state not in ("unknown", "unavailable")


async def test_unavailable_source_keeps_the_last_prices(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    before = hass.states.get(GAIN_TOMORROW).state
    hass.states.async_set(SOURCE, "unavailable", {})
    await hass.async_block_till_done()
    assert hass.states.get(GAIN_TOMORROW).state == before


async def test_source_without_prices_keeps_the_last_prices(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    before = hass.states.get(GAIN_TOMORROW).state
    hass.states.async_set(SOURCE, "3", {"prices": "garbage"})
    await hass.async_block_till_done()
    assert hass.states.get(GAIN_TOMORROW).state == before


async def test_bad_rows_do_not_stop_the_plan(hass, entry, freezer):
    freezer.move_to(NOW)
    good = rows(since="2026-11-09T12:00:00+02:00")
    publish(hass, [*good[:10], {"start": "oops"}, None, *good[10:]])
    await setup(hass, entry)
    assert float(hass.states.get(GAIN_TOMORROW).state) > 0


async def test_mixed_interval_lengths_use_the_common_one(hass, entry, freezer):
    freezer.move_to(NOW)
    prices = rows(since="2026-11-09T12:00:00+02:00")
    prices[5] = prices[5] | {"end": "2026-11-09T12:45:00+02:00"}  # one odd 30-minute interval
    publish(hass, prices)
    await setup(hass, entry)
    assert hass.states.get(GAIN_TOMORROW).state not in ("unknown", "unavailable")


@pytest.mark.parametrize("unit, monetary", [("EUR/kWh", True), ("cents/kWh", False), (None, False)])
async def test_units_come_from_the_source(hass, entry, freezer, unit, monetary):
    freezer.move_to(NOW)
    prices = rows(since="2026-11-09T12:00:00+02:00")
    hass.states.async_set(SOURCE, "1", {"prices": prices} | ({"unit": unit} if unit else {}))
    await setup(hass, entry)
    state = hass.states.get(GAIN_TOMORROW)
    assert (state.attributes.get("device_class") == "monetary") is monetary
    per_kwh = hass.states.get("sensor.battery_planner_net_gain_per_kwh_tomorrow")
    assert per_kwh.attributes.get("unit_of_measurement") == unit


@pytest.mark.parametrize(
    "soc, unit, expected",
    [("4.5", "kWh", 4.5), ("4500", "Wh", 4.5), ("50", "%", 5.0), ("150", "%", 10.0), ("-5", "%", 0.0), ("99", "kWh", 10.0)],
)
async def test_soc_units_and_clamping(hass, entry, freezer, soc, unit, expected):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.soc", soc, {"unit_of_measurement": unit})
    hass.config_entries.async_update_entry(entry, options={"battery_soc_entity": "sensor.soc"})
    await setup(hass, entry)
    assert hass.states.get(GAIN).attributes["starting_energy_kwh"] == pytest.approx(expected)


async def test_capacity_sensor_replaces_the_fixed_capacity(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.soc", "80", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.cap", "12000", {"unit_of_measurement": "Wh"})
    hass.config_entries.async_update_entry(
        entry, options={"battery_soc_entity": "sensor.soc", "battery_capacity_entity": "sensor.cap"}
    )
    await setup(hass, entry)
    attrs = hass.states.get(GAIN).attributes
    assert attrs["capacity_kwh"] == 12.0 and attrs["starting_energy_kwh"] == pytest.approx(9.6, abs=0.01)


@pytest.mark.parametrize("state", ["unavailable", "unknown", "abc", "nan", "inf"])
async def test_unusable_sensors_fall_back_to_an_empty_battery(hass, entry, freezer, state):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.soc", state, {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.cap", state, {"unit_of_measurement": "kWh"})
    hass.config_entries.async_update_entry(
        entry, options={"battery_soc_entity": "sensor.soc", "battery_capacity_entity": "sensor.cap"}
    )
    await setup(hass, entry)
    attrs = hass.states.get(GAIN).attributes
    assert attrs["starting_energy"] == "assumed empty" and attrs["capacity_kwh"] == 10.0


async def test_missing_and_zero_sensors_are_ignored(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.cap", "0", {"unit_of_measurement": "kWh"})
    hass.config_entries.async_update_entry(
        entry, options={"battery_soc_entity": "sensor.nope", "battery_capacity_entity": "sensor.cap"}
    )
    await setup(hass, entry)
    attrs = hass.states.get(GAIN).attributes
    assert attrs["starting_energy"] == "assumed empty" and attrs["capacity_kwh"] == 10.0


async def test_plan_follows_the_battery_level_every_quarter_hour(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.soc", "0", {"unit_of_measurement": "%"})
    hass.config_entries.async_update_entry(entry, options={"battery_soc_entity": "sensor.soc"})
    await setup(hass, entry)
    empty = float(hass.states.get(GAIN).state)
    hass.states.async_set("sensor.soc", "100", {"unit_of_measurement": "%"})
    freezer.move_to("2026-11-09 12:15:03+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(GAIN).state) > empty


async def test_battery_power_limits_reach_the_plan(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.config_entries.async_update_entry(
        entry, options={"battery_charge_power_kw": 0.3, "battery_discharge_power_kw": 1.2}
    )
    await setup(hass, entry)
    assert entry.runtime_data.battery.max_charge_kw == 0.3
    from datetime import datetime

    for block in hass.states.get(GAIN_TOMORROW).attributes["schedule"]:
        hours = (datetime.fromisoformat(block["to"]) - datetime.fromisoformat(block["from"])).total_seconds() / 3600
        limit = 0.3 if block["action"] == "charge" else 1.2
        assert block["kwh"] <= limit * hours + 0.01


async def test_unload_and_reload(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED and float(hass.states.get(GAIN_TOMORROW).state) > 0
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(GAIN_TOMORROW).state == "unavailable"
