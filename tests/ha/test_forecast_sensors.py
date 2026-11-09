"""Usable share, energy and state-of-charge sensors, and the countdown timers."""

from math import sqrt

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import NOW, publish, rows, setup

GAIN = "sensor.battery_planner_net_gain_today"
SOC = {"battery_soc_entity": "sensor.soc"}


def feed(hass, level="100", unit="%"):
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.states.async_set("sensor.soc", level, {"unit_of_measurement": unit})


# -- usable share -------------------------------------------------------------


@pytest.mark.parametrize(
    "level, available",
    [("100", 5.0), ("60", 1.0), ("50", 0.0), ("40", 0.0)],  # a 10 kWh battery, 5 kWh reserved
)
async def test_only_the_energy_above_the_reserve_is_available(hass, entry, freezer, level, available):
    freezer.move_to(NOW)
    feed(hass, level)
    hass.config_entries.async_update_entry(entry, options={**SOC, "battery_usable_pct": 50})
    await setup(hass, entry)
    attrs = hass.states.get(GAIN).attributes
    assert attrs["capacity_kwh"] == 5.0 and attrs["reserve_kwh"] == 5.0
    assert attrs["starting_energy_kwh"] == pytest.approx(available)


async def test_the_reserve_is_never_sold(hass, entry, freezer):
    freezer.move_to(NOW)
    feed(hass, "100")
    hass.config_entries.async_update_entry(entry, options={**SOC, "battery_usable_pct": 50})
    await setup(hass, entry)
    soc = hass.states.get("sensor.battery_planner_projected_state_of_charge_today")
    assert min(c["kwh"] for c in soc.attributes["curve"]) >= 5.0 - 1e-6  # the bottom half stays


async def test_full_share_changes_nothing(hass, entry, freezer):
    freezer.move_to(NOW)
    feed(hass, "100")
    hass.config_entries.async_update_entry(entry, options=SOC)
    await setup(hass, entry)
    attrs = hass.states.get(GAIN).attributes
    assert attrs["capacity_kwh"] == 10.0 and attrs["reserve_kwh"] == 0.0
    assert attrs["starting_energy_kwh"] == pytest.approx(10.0)


# -- energy and state of charge -----------------------------------------------


async def test_energy_sensors_match_the_schedule(hass, entry, freezer):
    freezer.move_to(NOW)
    feed(hass, "20")
    hass.config_entries.async_update_entry(entry, options=SOC)
    await setup(hass, entry)
    for day in ("today", "tomorrow"):
        gain = hass.states.get(f"sensor.battery_planner_net_gain_{day}").attributes
        charge = hass.states.get(f"sensor.battery_planner_energy_to_charge_{day}")
        export = hass.states.get(f"sensor.battery_planner_energy_to_export_{day}")
        assert export.attributes["unit_of_measurement"] == charge.attributes["unit_of_measurement"] == "kWh"
        assert float(export.state) == pytest.approx(gain["energy_sold_kwh"], abs=0.01)
        assert float(charge.state) == pytest.approx(gain["energy_bought_kwh"], abs=0.01)
        assert sum(w["kwh"] for w in export.attributes["windows"]) == pytest.approx(float(export.state), abs=0.01)
        assert sum(w["kwh"] for w in charge.attributes["windows"]) == pytest.approx(float(charge.state), abs=0.01)
    # Tomorrow starts empty: the cheap night is where it charges.
    night = hass.states.get("sensor.battery_planner_energy_to_charge_tomorrow").attributes["windows"]
    assert night and all(w["from"].startswith("2026-11-10T0") for w in night)


async def test_projected_soc_is_consistent_with_the_plan(hass, entry, freezer):
    freezer.move_to(NOW)
    feed(hass, "20")
    hass.config_entries.async_update_entry(entry, options=SOC)
    await setup(hass, entry)
    for day in ("today", "tomorrow"):
        soc = hass.states.get(f"sensor.battery_planner_projected_state_of_charge_{day}")
        gain = hass.states.get(f"sensor.battery_planner_net_gain_{day}").attributes
        a = soc.attributes
        assert soc.attributes["unit_of_measurement"] == "%"
        assert a["start_kwh"] == pytest.approx(gain["starting_energy_kwh"], abs=0.01)
        one_way = sqrt(0.9)
        expected = a["start_kwh"] + gain["energy_bought_kwh"] * one_way - gain["energy_sold_kwh"] / one_way
        assert a["end_kwh"] == pytest.approx(expected, abs=0.05)
        assert float(soc.state) == pytest.approx(a["end_kwh"] / a["battery_kwh"] * 100, abs=0.1)
        assert all(0 <= c["kwh"] <= a["battery_kwh"] + 1e-6 for c in a["curve"])
        assert a["peak_kwh"] == max(c["kwh"] for c in a["curve"])
    today = hass.states.get("sensor.battery_planner_projected_state_of_charge_today").attributes
    assert today["curve"][0]["kwh"] == pytest.approx(2.0)  # starts at the real level, 20% of 10 kWh


async def test_energy_sensors_are_unknown_without_prices(hass, entry, freezer):
    freezer.move_to(NOW)
    hass.states.async_set("sensor.prices", "0", {"unit": "RON/kWh", "prices": []})
    await setup(hass, entry)
    assert hass.states.get("sensor.battery_planner_energy_to_charge_today").state == "unknown"
    assert hass.states.get("sensor.battery_planner_projected_state_of_charge_today").state == "unknown"


# -- countdown timers ---------------------------------------------------------

CHEAPEST = "sensor.battery_planner_time_to_cheapest_price"
BEST_SELL = "sensor.battery_planner_time_to_best_sell_price"


async def test_countdowns_point_at_the_right_intervals(hass, entry, freezer):
    freezer.move_to(NOW)  # 12:00:30
    feed(hass)
    await setup(hass, entry)
    # Cheapest buy: 02:00 tomorrow = 13 h 59 min 30 s away -> 840 (rounded up).
    # Best sell: the first dear interval, 18:00 today = 5 h 59 min 30 s -> 360.
    assert hass.states.get(CHEAPEST).state == "840"
    assert hass.states.get(BEST_SELL).state == "360"
    attrs = hass.states.get(CHEAPEST).attributes
    assert attrs["at"].startswith("2026-11-10T02:00:00+02:00") and attrs["price"] == pytest.approx(0.3 * 1.21)
    assert hass.states.get(BEST_SELL).attributes["price"] == pytest.approx(2.0)
    assert hass.states.get(BEST_SELL).attributes["unit_of_measurement"] == "min"


async def test_countdown_ticks_every_minute(hass, entry, freezer):
    freezer.move_to(NOW)
    feed(hass)
    await setup(hass, entry)
    assert hass.states.get(BEST_SELL).state == "360"
    freezer.move_to("2026-11-09 12:01:31+02:00")  # 5 h 58 min 29 s to go
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(BEST_SELL).state == "359"


async def test_countdown_is_zero_during_the_best_interval(hass, entry, freezer):
    freezer.move_to("2026-11-09 18:05:00+02:00")
    publish(hass, rows(since="2026-11-09T18:00:00+02:00"))
    await setup(hass, entry)
    assert hass.states.get(BEST_SELL).state == "0"


async def test_countdown_horizon_limits_the_search(hass, entry, freezer):
    freezer.move_to(NOW)
    feed(hass)
    hass.config_entries.async_update_entry(entry, options={"timer_horizon_hours": 6})
    await setup(hass, entry)
    # Within 6 h the cheapest buy is just the current price level: the first interval.
    assert hass.states.get(CHEAPEST).state == "0"
    assert hass.states.get(BEST_SELL).state == "360"  # 18:00 is inside 6 h (5 h 59 min)


async def test_countdown_without_prices_is_unknown(hass, entry, freezer):
    freezer.move_to(NOW)
    hass.states.async_set("sensor.prices", "0", {"unit": "RON/kWh", "prices": []})
    await setup(hass, entry)
    assert hass.states.get(CHEAPEST).state == "unknown"


async def test_ties_pick_the_earliest_interval(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(low=0.5, mid=0.5, high=0.5, since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    assert hass.states.get(CHEAPEST).state == "0"
    assert hass.states.get(BEST_SELL).state == "0"
