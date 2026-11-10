"""The free energy signal: when heavy loads cost nothing."""

from datetime import datetime, timedelta, timezone

from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.battery_planner import solar

from .conftest import NOW, publish, rows, setup
from .test_consumption import import_hours

FREE = "binary_sensor.battery_planner_free_energy"
MODE = "select.battery_planner_planning_mode"
EET = timezone(timedelta(hours=2))
BASE = {
    "battery_capacity_kwh": 1, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5,
    "battery_degradation_per_kwh": 0.1, "house_power_entity": "sensor.house", "battery_soc_entity": "sensor.soc",
}


def prices(sell=None, buy=None, hours=(10, 16), day="2026-11-09"):
    """The usual price curve, with sell and buy replaced between ``hours`` on ``day``."""
    out = rows(since="2026-11-09T12:00:00+02:00")
    for r in out:
        if r["start"].startswith(day) and hours[0] <= int(r["start"][11:13]) < hours[1]:
            if sell is not None:
                r["sell"] = sell
            if buy is not None:
                r["buy"] = buy
    return out


def sun(day=9, wh=3000):
    return {datetime(2026, 11, day, h, tzinfo=EET).astimezone(timezone.utc).isoformat(): wh for h in range(9, 17)}


async def start(hass, entry, freezer, monkeypatch, series, forecast=None, options=None, soc="100"):
    freezer.move_to(NOW)
    publish(hass, series)
    import_hours(hass, "sensor.house", lambda _: 0.8, unit="kW")
    await async_wait_recording_done(hass)
    hass.states.async_set("sensor.house", "0.8", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.soc", soc, {"unit_of_measurement": "%"})

    async def fetch(_hass):
        return solar.to_intervals("fake", forecast or {})

    async def has(_hass):
        return bool(forecast)

    monkeypatch.setattr(solar, "fetch_forecast", fetch)
    monkeypatch.setattr(solar, "has_forecast", has)
    hass.config_entries.async_update_entry(entry, options={**BASE, **(options or {})})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "balanced"}, blocking=True)
    await hass.async_block_till_done()


async def test_an_ordinary_day_is_not_free(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, prices())
    state = hass.states.get(FREE)
    assert state.state == "off" and state.attributes["windows"] == []


async def test_a_buy_price_at_zero_or_below_is_free(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, prices(buy=-0.3))
    state = hass.states.get(FREE)
    assert state.state == "on"
    assert state.attributes["reason"] == "buy price at or below zero"
    assert state.attributes["until"].startswith("2026-11-09T16:00")


async def test_surplus_sun_that_nobody_pays_for_is_free(hass, entry, freezer, monkeypatch):
    # A full 1 kWh battery cannot take 2 kW of surplus, and the sell price is negative.
    await start(hass, entry, freezer, monkeypatch, prices(sell=-0.2, buy=0.5), forecast=sun())
    state = hass.states.get(FREE)
    assert state.state == "on"
    assert state.attributes["reason"] in (
        "solar surplus that would be exported for nothing", "solar surplus beyond what can be stored or exported"
    )
    assert state.attributes["windows"][0]["surplus_kwh"] > 1.0


async def test_it_is_not_free_when_the_battery_takes_the_sun(hass, entry, freezer, monkeypatch):
    # An emptier, larger battery stores the surplus: it is not wasted.
    await start(hass, entry, freezer, monkeypatch, prices(sell=-0.2, buy=0.5), forecast=sun(),
                options={"battery_capacity_kwh": 20}, soc="10")
    assert hass.states.get(FREE).state == "off"


async def test_a_zero_sell_price_with_surplus_is_free_too(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, prices(sell=0.0, buy=0.5), forecast=sun())
    assert hass.states.get(FREE).state == "on"


async def test_the_next_free_stretch_is_announced(hass, entry, freezer, monkeypatch):
    # Nothing today, but tomorrow between 11:00 and 14:00 the buy price drops to zero.
    series = prices(buy=-0.1, hours=(11, 14), day="2026-11-10")
    await start(hass, entry, freezer, monkeypatch, series)
    state = hass.states.get(FREE)
    assert state.state == "off"
    assert state.attributes["next_start"].startswith("2026-11-10T11:00")
    assert state.attributes["next_end"].startswith("2026-11-10T14:00")
    assert [w["from"][:16] for w in state.attributes["windows"]] == ["2026-11-10T11:00"]  # one merged stretch
