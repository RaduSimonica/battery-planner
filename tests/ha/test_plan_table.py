"""The plan interval by interval, and how it follows what is really happening."""

from datetime import datetime

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .conftest import NOW, publish, rows, setup
from .test_consumption import import_hours

PLAN = "sensor.battery_planner_plan"
GAIN = "sensor.battery_planner_net_gain"
HOUSE = {"house_power_entity": "sensor.house"}
OPTIONS = {
    "battery_capacity_kwh": 10, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5,
    "battery_degradation_per_kwh": 0.1, "battery_soc_entity": "sensor.soc",
}


async def start(hass, entry, freezer, house_kw=0.8, soc="100"):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    import_hours(hass, "sensor.house", lambda _: 0.8, unit="kW")  # the usual 0.8 kW
    await async_wait_recording_done(hass)
    hass.states.async_set("sensor.house", str(house_kw), {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.soc", soc, {"unit_of_measurement": "%"})
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, **HOUSE})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()


def table(hass):
    return hass.states.get(PLAN).attributes["plan"]


async def test_one_row_per_interval_with_everything_a_chart_needs(hass, entry, freezer):
    await start(hass, entry, freezer)
    state = hass.states.get(PLAN)
    plan = state.attributes["plan"]
    assert int(state.state) == len(plan) == 48 + 96  # the rest of today, then tomorrow
    first = plan[0]
    assert first["start"].startswith("2026-11-09T12:00:00") and first["end"].startswith("2026-11-09T12:15:00")
    for key in ("buy", "sell", "action", "charge_kwh", "discharge_kwh", "to_house_kwh", "export_kwh",
                "load_kwh", "soc_kwh", "soc_pct", "earnings", "expenses", "net"):
        assert key in first
    assert {r["action"] for r in plan} <= {"charge", "discharge", "idle"}
    assert all(a["end"] == b["start"] for a, b in zip(plan, plan[1:]))  # no gaps
    assert all(0 <= r["soc_pct"] <= 100 for r in plan)
    assert state.attributes["battery_kwh"] == 10 and state.attributes["now_soc_pct"] == 100.0


async def test_actions_match_the_energy(hass, entry, freezer):
    await start(hass, entry, freezer)
    plan = table(hass)
    assert any(r["action"] == "discharge" for r in plan)
    for r in plan:
        if r["action"] == "charge":
            assert r["charge_kwh"] > 0 and r["discharge_kwh"] == 0
        elif r["action"] == "discharge":
            assert r["discharge_kwh"] > 0 and r["charge_kwh"] == 0
            assert r["export_kwh"] == pytest.approx(r["discharge_kwh"] - r["to_house_kwh"], abs=1e-3)
        else:
            assert r["charge_kwh"] == r["discharge_kwh"] == 0


async def test_the_running_totals_add_up_across_both_days(hass, entry, freezer):
    await start(hass, entry, freezer)
    plan = table(hass)
    total = sum(float(hass.states.get(f"{GAIN}_{d}").state) for d in ("today", "tomorrow"))
    assert plan[-1]["net"] == pytest.approx(total, abs=0.01)
    assert plan[-1]["earnings"] - plan[-1]["expenses"] >= plan[-1]["net"] - 1e-6  # wear is on top
    assert all(b["earnings"] >= a["earnings"] - 1e-6 for a, b in zip(plan, plan[1:]))


async def test_tomorrow_starts_where_today_ends(hass, entry, freezer):
    await start(hass, entry, freezer)
    plan = table(hass)
    last_today = next(i for i, r in enumerate(plan) if r["start"].startswith("2026-11-10")) - 1
    tomorrow = hass.states.get(f"{GAIN}_tomorrow").attributes
    assert tomorrow["starting_energy"] == "end of today's plan"
    assert tomorrow["starting_energy_kwh"] == pytest.approx(plan[last_today]["soc_kwh"], abs=0.01)


async def test_what_the_house_does_now_shapes_the_next_hours(hass, entry, freezer):
    await start(hass, entry, freezer, house_kw=3.0)  # an oven is on: 3 kW against the usual 0.8
    plan = table(hass)
    assert hass.states.get(PLAN).attributes["house_load_shift_kw"] == pytest.approx(2.2, abs=0.05)
    assert plan[0]["load_kwh"] == pytest.approx(3.0 * 0.25, abs=0.02)  # now: what is really used
    assert plan[16]["load_kwh"] == pytest.approx(0.8 * 0.25, abs=0.06)  # four hours on: the usual again
    assert plan[4]["load_kwh"] > plan[16]["load_kwh"]  # fading in between


async def test_a_drop_in_the_battery_level_plans_again_early(hass, entry, freezer):
    await start(hass, entry, freezer)
    freezer.move_to("2026-11-09 12:06:30+02:00")  # between two 15-minute steps
    hass.states.async_set("sensor.soc", "60", {"unit_of_measurement": "%"})  # 4 kWh less than planned
    await hass.async_block_till_done()
    assert hass.states.get(f"{GAIN}_today").attributes["starting_energy_kwh"] == pytest.approx(6.0)


async def test_a_small_change_in_the_battery_level_does_not(hass, entry, freezer):
    await start(hass, entry, freezer)
    freezer.move_to("2026-11-09 12:06:30+02:00")
    hass.states.async_set("sensor.soc", "95", {"unit_of_measurement": "%"})  # 0.5 kWh
    await hass.async_block_till_done()
    assert hass.states.get(f"{GAIN}_today").attributes["starting_energy_kwh"] == pytest.approx(10.0)


async def test_a_clearly_higher_house_load_plans_again_early(hass, entry, freezer):
    await start(hass, entry, freezer)
    assert "house_load_shift_kw" in hass.states.get(PLAN).attributes  # 0 shift to begin with
    before = hass.states.get(PLAN).attributes["house_load_shift_kw"]
    freezer.move_to("2026-11-09 12:06:30+02:00")
    hass.states.async_set("sensor.house", "4.0", {"unit_of_measurement": "kW"})  # the washer and the oven
    await hass.async_block_till_done()
    assert hass.states.get(PLAN).attributes["house_load_shift_kw"] > before + 0.5


async def test_early_plans_are_spaced_out(hass, entry, freezer):
    await start(hass, entry, freezer)
    freezer.move_to("2026-11-09 12:06:30+02:00")
    hass.states.async_set("sensor.soc", "60", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    hass.states.async_set("sensor.soc", "30", {"unit_of_measurement": "%"})  # a minute later: too soon
    freezer.move_to(datetime.fromisoformat("2026-11-09T12:07:30+02:00"))
    await hass.async_block_till_done()
    assert hass.states.get(f"{GAIN}_today").attributes["starting_energy_kwh"] == pytest.approx(6.0)
    freezer.move_to(datetime.fromisoformat("2026-11-09T12:12:40+02:00"))  # five minutes on
    hass.states.async_set("sensor.soc", "29", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert hass.states.get(f"{GAIN}_today").attributes["starting_energy_kwh"] == pytest.approx(2.9)
