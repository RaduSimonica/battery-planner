"""Solar in the plan: the Energy dashboard's forecasts, the sensors, and what the panels make now."""

from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.battery_planner import solar

from .conftest import NOW, publish, rows, setup
from .test_consumption import import_hours

PLAN = "sensor.battery_planner_plan"
GAIN = "sensor.battery_planner_net_gain"
MODE = "select.battery_planner_planning_mode"
OPTIONS = {
    "battery_capacity_kwh": 10, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5,
    "battery_degradation_per_kwh": 0.1, "house_power_entity": "sensor.house",
}
EET = timezone(timedelta(hours=2))


def sunny_tomorrow(peak_wh=3000):
    """Wh per hour on 10 November, a bell from 08:00 to 16:00 local time: {period start: Wh}."""
    out = {}
    for h in range(8, 16):
        out[datetime(2026, 11, 10, h, tzinfo=EET).astimezone(timezone.utc)] = peak_wh * (1 - abs(h + 0.5 - 12) / 4.5)
    return out


def as_forecast(wh):
    """What the provider reports, as the planner reads it (start-aligned, like most providers)."""
    return solar.to_intervals("fake", {k.isoformat(): v for k, v in wh.items()})


async def start(hass, entry, freezer, monkeypatch, forecast, options=None, mode="balanced", states=None):
    freezer.move_to(NOW)
    # A summer-like price curve: cheap at noon (when everybody exports), dear in the evening.
    publish(hass, rows(cheap=(10, 15), low=0.05, dear=(18, 22), high=2.0, mid=0.9, since="2026-11-09T12:00:00+02:00"))
    import_hours(hass, "sensor.house", lambda _: 0.8, unit="kW")
    await async_wait_recording_done(hass)
    hass.states.async_set("sensor.house", "0.8", {"unit_of_measurement": "kW"})
    for entity, (value, unit) in (states or {}).items():
        hass.states.async_set(entity, str(value), {"unit_of_measurement": unit})

    async def fetch(_hass):
        return as_forecast(forecast)

    async def has(_hass):
        return bool(forecast)

    monkeypatch.setattr(solar, "fetch_forecast", fetch)
    monkeypatch.setattr(solar, "has_forecast", has)
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, **(options or {})})
    await setup(hass, entry)
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": mode}, blocking=True)
    await hass.async_block_till_done()


def table(hass):
    return hass.states.get(PLAN).attributes["plan"]


async def test_the_forecast_shows_in_the_plan_and_in_two_sensors(hass, entry, freezer, monkeypatch):
    forecast = sunny_tomorrow()
    await start(hass, entry, freezer, monkeypatch, forecast)
    noon = next(r for r in table(hass) if r["start"].startswith("2026-11-10T12:00"))
    assert noon["solar_kwh"] == pytest.approx(forecast[datetime(2026, 11, 10, 12, tzinfo=EET).astimezone(timezone.utc)] / 4000, abs=0.01)
    total = sum(forecast.values()) / 1000
    assert float(hass.states.get("sensor.battery_planner_expected_solar_tomorrow").state) == pytest.approx(total, abs=0.05)
    assert float(hass.states.get("sensor.battery_planner_expected_solar_today").state) == pytest.approx(0.0, abs=0.01)


async def test_no_forecast_no_solar_sensors(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, {})
    assert hass.states.get("sensor.battery_planner_expected_solar_tomorrow") is None
    assert all(r["solar_kwh"] == 0 for r in table(hass))


async def test_the_sun_fills_the_battery_instead_of_the_grid(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, sunny_tomorrow())
    tomorrow = hass.states.get(f"{GAIN}_tomorrow").attributes
    assert tomorrow["energy_from_solar_kwh"] > 3.0
    assert any(r["solar_to_battery_kwh"] > 0 for r in table(hass))


async def test_solar_lowers_what_is_bought_from_the_grid(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, {})
    without = hass.states.get(f"{GAIN}_tomorrow").attributes["energy_bought_kwh"]
    # a fresh instance with sun
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    async def fetch(_hass):
        return as_forecast(sunny_tomorrow())

    async def has(_hass):
        return True

    monkeypatch.setattr(solar, "fetch_forecast", fetch)
    monkeypatch.setattr(solar, "has_forecast", has)
    await hass.config_entries.async_setup(entry.entry_id)
    await async_wait_recording_done(hass)  # the consumption forecast is learned in the background
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": "balanced"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(f"{GAIN}_tomorrow").attributes["mode"] == "balanced"
    with_sun = hass.states.get(f"{GAIN}_tomorrow").attributes["energy_bought_kwh"]
    assert with_sun < without


async def test_max_profit_does_not_use_the_panels(hass, entry, freezer, monkeypatch):
    await start(hass, entry, freezer, monkeypatch, sunny_tomorrow(), mode="max_profit")
    assert hass.states.get(f"{GAIN}_tomorrow").attributes["energy_from_solar_kwh"] == 0
    assert any(r["solar_kwh"] > 0 for r in table(hass))  # still shown, for the charts


async def test_what_the_panels_make_now_corrects_the_next_hour(hass, entry, freezer, monkeypatch):
    # Today the forecast expects 2 kW at 12:00; the panels (two of them) make 0.6 kW in total.
    today = {datetime(2026, 11, 9, h, tzinfo=EET).astimezone(timezone.utc): 2000.0 for h in range(10, 15)}
    await start(
        hass, entry, freezer, monkeypatch, today,
        options={"grid_power_entity": "sensor.grid", "solar_power_entity": ["sensor.pv1", "sensor.pv2"]},
        states={"sensor.grid": (0, "W"), "sensor.pv1": (400, "W"), "sensor.pv2": (200, "W")},
    )
    first = table(hass)[0]
    assert first["solar_kwh"] == pytest.approx(2.0 / 4 * 0.3, abs=0.03)  # 0.6 of 2 kW, over a quarter hour
    later = table(hass)[8]  # two hours on, mostly recovered
    assert later["solar_kwh"] > 0.4


async def test_several_energy_providers_are_added_up(hass, monkeypatch):
    class Provider:
        def __init__(self, wh):
            self.wh = wh

        async def __call__(self, _hass, _entry_id):
            return {"wh_hours": self.wh}

    stamp = "2026-11-10T10:00:00+02:00"
    one = MockConfigEntry(domain="fake_a", entry_id="a")
    two = MockConfigEntry(domain="fake_b", entry_id="b")
    gone = MockConfigEntry(domain="fake_c", entry_id="c")
    for e in (one, two, gone):
        e.add_to_hass(hass)

    async def entries(_hass):
        return ["a", "b", "c", "missing"]

    async def platforms(_hass):
        return {"fake_a": Provider({stamp: 1000}), "fake_b": Provider({stamp: 500, "2026-11-10T11:00:00+02:00": 200})}

    monkeypatch.setattr(solar, "_forecast_entries", entries)
    monkeypatch.setattr("homeassistant.components.energy.websocket_api.async_get_energy_platforms", platforms)
    intervals = await solar.fetch_forecast(hass)
    assert sum(kwh for _s, _e, kwh in intervals) == pytest.approx(1.7)  # both providers, added up
    noon = datetime.fromisoformat("2026-11-10T10:00:00+02:00").timestamp()
    covering = [kwh for begin, end, kwh in intervals if begin <= noon < end]
    assert sorted(covering) == [0.5, 1.0]  # two providers over the same hour
