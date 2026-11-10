"""House consumption: sensors, learning and the options page."""

from datetime import timedelta

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .conftest import NOW, publish, rows, setup

POWER = "sensor.battery_planner_house_power"
EXPECTED = "sensor.battery_planner_expected_consumption_next_24_hours"
TODAY = "sensor.battery_planner_consumption_today"
GRID = {"grid_power_entity": "sensor.grid"}


def watts(hass, entity, value, unit="W"):
    hass.states.async_set(entity, str(value), {"unit_of_measurement": unit})


async def start(hass, entry, freezer, options, **states):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    for entity, (value, unit) in states.items():
        watts(hass, entity.replace("__", "."), value, unit)
    hass.config_entries.async_update_entry(entry, options=options)
    await setup(hass, entry)


async def test_no_sensors_no_consumption_entities(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    await setup(hass, entry)
    assert hass.states.get(POWER) is None and hass.states.get(EXPECTED) is None


async def test_house_power_from_a_signed_grid_sensor(hass, entry, freezer):
    await start(hass, entry, freezer, GRID, sensor__grid=(1500, "W"))
    assert float(hass.states.get(POWER).state) == pytest.approx(1.5)
    assert hass.states.get(POWER).attributes["unit_of_measurement"] == "kW"
    watts(hass, "sensor.grid", -2, "kW")  # exporting
    await hass.async_block_till_done()
    assert float(hass.states.get(POWER).state) == 0.0


async def test_flip_and_the_other_sensors(hass, entry, freezer):
    opts = {**GRID, "grid_power_flip": True, "solar_power_entity": "sensor.pv", "battery_power_entity": "sensor.bat"}
    # Reports export as positive: exporting 1 kW. Solar 3 kW, battery takes 0.5 kW: house 1.5 kW.
    await start(hass, entry, freezer, opts, sensor__grid=(1, "kW"), sensor__pv=(3000, "W"), sensor__bat=(500, "W"))
    assert float(hass.states.get(POWER).state) == pytest.approx(1.5)


async def test_import_export_pair(hass, entry, freezer):
    opts = {"grid_import_entity": "sensor.imp", "grid_export_entity": "sensor.exp"}
    await start(hass, entry, freezer, opts, sensor__imp=(800, "W"), sensor__exp=(0, "W"))
    assert float(hass.states.get(POWER).state) == pytest.approx(0.8)


async def test_a_sensor_in_the_wrong_unit_is_ignored_with_one_warning(hass, entry, freezer, caplog):
    await start(hass, entry, freezer, GRID, sensor__grid=(5, "A"))
    assert hass.states.get(POWER).state == "unknown"
    watts(hass, "sensor.grid", 6, "A")
    await hass.async_block_till_done()
    assert caplog.text.count("sensor.grid reports A") == 1


def import_hours(hass, entity, kw, unit="W", days=14, state_class=True):
    """Hourly statistics for ``entity``: ``kw`` (in kW, shown in ``unit``) for the last ``days`` days."""
    from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
    from homeassistant.components.recorder.statistics import async_import_statistics
    from homeassistant.util import dt as dt_util

    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        mean = {"mean_type": StatisticMeanType.ARITHMETIC}
    except ImportError:  # older Home Assistant
        mean = {"has_mean": True}
    factor = {"W": 1000, "kW": 1}[unit]
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    rows = [
        StatisticData(start=end - timedelta(hours=h), mean=kw(end - timedelta(hours=h)) * factor)
        for h in range(1, 24 * days)
    ]
    async_import_statistics(
        hass,
        StatisticMetaData(
            has_sum=False, name=entity, source="recorder", statistic_id=entity,
            unit_of_measurement=unit, unit_class="power" if unit in ("W", "kW") else None, **mean,
        ),
        rows,
    )


async def test_it_learns_from_the_recorders_history(hass, entry, freezer):
    freezer.move_to(NOW)
    import_hours(hass, "sensor.grid", lambda _: 1.0)  # 1 kW, reported in W
    await async_wait_recording_done(hass)
    await start(hass, entry, freezer, GRID, sensor__grid=(1000, "W"))
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    state = hass.states.get(EXPECTED)
    assert float(state.state) == pytest.approx(24.0)  # 1 kW for 24 hours
    assert state.attributes["hours_known"] == 24 and len(state.attributes["forecast"]) == 24
    assert state.attributes["days_learned"] >= 13


async def test_the_ev_charger_is_taken_out_of_the_history(hass, entry, freezer):
    freezer.move_to(NOW)
    import_hours(hass, "sensor.grid", lambda _: 3.0)  # the grid sees 3 kW
    import_hours(hass, "sensor.ev", lambda _: 2.0, unit="kW")  # of which 2 kW is the car
    await async_wait_recording_done(hass)
    await start(hass, entry, freezer, {**GRID, "ev_power_entity": "sensor.ev"}, sensor__grid=(3000, "W"), sensor__ev=(2, "kW"))
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(EXPECTED).state) == pytest.approx(24.0)  # 1 kW of house, not 3
    assert float(hass.states.get(POWER).state) == pytest.approx(1.0)  # live value agrees


async def test_a_sensor_without_statistics_is_reported_once(hass, entry, freezer, caplog):
    await start(hass, entry, freezer, GRID, sensor__grid=(1000, "W"))
    await async_wait_recording_done(hass)
    await hass.async_block_till_done()
    assert hass.states.get(EXPECTED).state == "unknown"
    assert caplog.text.count("sensor.grid has no long-term statistics") == 1


# -- options page -------------------------------------------------------------


async def open_page(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "consumption"})
    assert result["step_id"] == "consumption"
    return result


@pytest.mark.parametrize(
    "values, error",
    [
        ({"grid_power_entity": "sensor.g", "grid_import_entity": "sensor.i", "grid_export_entity": "sensor.e"}, "grid_both"),
        ({"grid_import_entity": "sensor.i"}, "grid_pair"),
        ({"solar_power_entity": "sensor.pv"}, "grid_missing"),
    ],
)
async def test_the_page_refuses_what_cannot_work(hass, entry, freezer, values, error):
    result = await open_page(hass, entry, freezer)
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}


async def test_the_page_saves_and_clears(hass, entry, freezer):
    result = await open_page(hass, entry, freezer)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"grid_power_entity": "sensor.g", "grid_power_flip": True}
    )
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "finish"})
    assert entry.options["grid_power_entity"] == "sensor.g" and entry.options["grid_power_flip"] is True

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "consumption"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})  # emptied
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "finish"})
    assert "grid_power_entity" not in entry.options


async def test_the_battery_page_leaves_consumption_alone(hass, entry, freezer):
    freezer.move_to(NOW)
    publish(hass, rows(since="2026-11-09T12:00:00+02:00"))
    hass.config_entries.async_update_entry(entry, options={**GRID})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "battery"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"battery_capacity_kwh": 10, "battery_charge_power_kw": 5, "battery_discharge_power_kw": 5,
         "battery_round_trip_efficiency_pct": 90, "battery_degradation_per_kwh": 0.15, "battery_usable_pct": 100},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "finish"})
    assert entry.options["grid_power_entity"] == "sensor.grid"
