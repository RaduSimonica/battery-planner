"""A car that follows the solar surplus: what the plan expects it to take."""

from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.battery_planner import solar

from .conftest import NOW, publish, rows, setup
from .test_consumption import import_hours

PLAN = "sensor.battery_planner_plan"
MODE = "select.battery_planner_planning_mode"
EET = timezone(timedelta(hours=2))
OPTIONS = {
    "battery_capacity_kwh": 40, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5,
    "battery_degradation_per_kwh": 0.1, "house_power_entity": "sensor.house",
    "solar_power_entity": ["sensor.pv"], "ev_power_entity": "sensor.ev",
}


def sunny(day):
    """6.5 kW from 10:00 to 14:00 local time (6500 Wh in each hour)."""
    return {datetime(2026, 11, day, h, tzinfo=EET).isoformat(): 6500 for h in range(10, 14)}


async def start(hass, entry, freezer, monkeypatch, options=None, ev_kw=0.0, pv_kw=0.0, days=(9, 10)):
    freezer.move_to(NOW)
    # A summer-like price curve: cheap at noon, dear in the evening.
    publish(hass, rows(cheap=(10, 14), low=0.05, dear=(18, 22), high=2.0, mid=0.9, since="2026-11-09T12:00:00+02:00"))
    sun_hours = lambda t: 6.5 if 10 <= t.hour + 2 < 14 else 0.0  # noqa: E731 (history is in UTC)
    # The car charges on every other day, when the sun is there. The house sensor counts the car too.
    car = lambda t: 5.0 if sun_hours(t) and t.toordinal() % 2 else 0.0  # noqa: E731
    import_hours(hass, "sensor.house", lambda t: 0.8 + car(t), unit="kW")
    import_hours(hass, "sensor.pv", sun_hours, unit="kW")
    import_hours(hass, "sensor.ev", car, unit="kW")
    await async_wait_recording_done(hass)
    for entity, value in (("sensor.house", 0.8 + ev_kw), ("sensor.pv", pv_kw), ("sensor.ev", ev_kw)):
        hass.states.async_set(entity, str(value), {"unit_of_measurement": "kW"})
    wh = {}
    for d in days:
        wh |= sunny(d)

    async def fetch(_hass):
        return solar.to_intervals("fake", wh)

    async def has(_hass):
        return True

    monkeypatch.setattr(solar, "fetch_forecast", fetch)
    monkeypatch.setattr(solar, "has_forecast", has)
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, **(options or {})})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "balanced"}, blocking=True)
    await hass.async_block_till_done()


def noon(hass, day):
    return next(r for r in hass.states.get(PLAN).attributes["plan"] if r["start"].startswith(f"2026-11-{day:02d}T12:00"))


async def test_the_car_is_expected_to_take_part_of_tomorrows_sun(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch)
    row = noon(hass, 10)
    # 6.5 kW of sun, 0.8 house: 5.7 surplus, 4.7 after the battery's kW, as often as the car charged.
    probability = entry.runtime_data.ev_probability
    assert probability == pytest.approx(0.5, abs=0.15)
    assert row["ev_kwh"] == pytest.approx(probability * 4.7 * 0.25, abs=0.01)
    assert row["solar_kwh"] == pytest.approx(6.5 * 0.25)  # the forecast itself is unchanged
    tomorrow = hass.states.get("sensor.battery_planner_net_gain_tomorrow").attributes
    plan = hass.states.get(PLAN).attributes["plan"]
    assert tomorrow["expected_ev_kwh"] == pytest.approx(sum(r["ev_kwh"] for r in plan if r["start"].startswith("2026-11-10")), abs=0.05)


async def test_less_sun_reaches_the_battery(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch)
    with_car = hass.states.get("sensor.battery_planner_net_gain_tomorrow").attributes["energy_from_solar_kwh"]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "ev_takes_surplus": False})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "balanced"}, blocking=True)
    await hass.async_block_till_done()
    without = hass.states.get("sensor.battery_planner_net_gain_tomorrow").attributes["energy_from_solar_kwh"]
    assert with_car < without - 3.0
    assert noon(hass, 10)["ev_kwh"] == 0


async def test_a_car_charging_now_is_expected_to_go_on(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, ev_kw=5.0, pv_kw=6.5)  # sunny now, as forecast
    first = noon(hass, 9)  # 12:00 today, the car is drawing 5 kW
    assert first["ev_kwh"] == pytest.approx(4.7 * 0.25, abs=0.05)  # all there is after the battery's share, not the average
    later = next(r for r in hass.states.get(PLAN).attributes["plan"] if r["start"].startswith("2026-11-09T13:45"))
    assert later["ev_kwh"] < first["ev_kwh"]


async def test_the_option_is_off_without_an_ev_sensor(hass, entry, freezer, monkeypatch):
    opts = {k: v for k, v in OPTIONS.items() if k != "ev_power_entity"}
    await start(hass, entry, freezer, monkeypatch, options=None)
    await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, options=opts)
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    assert entry.runtime_data.ev_settings is None
    assert all(r["ev_kwh"] == 0 for r in hass.states.get(PLAN).attributes["plan"])


async def test_diagnostics_show_what_was_learned(hass, entry, freezer, monkeypatch):
    from custom_components.battery_planner.diagnostics import async_get_config_entry_diagnostics

    await start(hass, entry, freezer, monkeypatch)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["ev_surplus"]["settings"] == {"start_kw": 4.6, "max_kw": 11.0, "battery_kw": 1.0}
    assert diag["ev_surplus"]["probability"] == pytest.approx(0.5, abs=0.15)
    assert diag["ev_surplus"]["hours_with_big_surplus"] >= 8


async def test_the_free_energy_signal_leaves_the_cars_share_out(hass, entry, freezer, monkeypatch):
    # A small battery that is full, and exports earn nothing: surplus sun would be free energy,
    # but the car takes most of it, so there is little left over.
    from custom_components.battery_planner.binary_sensor import free_windows

    publish(hass, [{**r, "sell": 0.0} for r in rows(since="2026-11-09T12:00:00+02:00")])
    await start(hass, entry, freezer, monkeypatch, options={"battery_capacity_kwh": 1})
    windows = free_windows(entry.runtime_data.data, entry.runtime_data.battery)
    with_car = sum(w["kwh"] for w in windows)
    await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "battery_capacity_kwh": 1, "ev_takes_surplus": False})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    without = sum(w["kwh"] for w in free_windows(entry.runtime_data.data, entry.runtime_data.battery))
    assert without > 0 and with_car < without
