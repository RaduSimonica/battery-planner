"""House power from sensors, and the learned daily pattern."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.battery_planner.consumption import (
    HOUR,
    LoadProfile,
    LoadSources,
    hourly_kwh,
    house_power_kw,
)

TZ = ZoneInfo("Europe/Bucharest")


def reader(**values):
    return lambda entity: values.get(entity)


# -- house power ---------------------------------------------------------------


def test_house_sensor_is_used_as_is():
    src = LoadSources(house="h", grid="g")
    assert house_power_kw(src, reader(h=1.2, g=9.0)) == 1.2


def test_signed_grid_sensor_and_flip():
    src = LoadSources(grid="g")
    assert house_power_kw(src, reader(g=0.8)) == pytest.approx(0.8)
    assert house_power_kw(src, reader(g=-0.8)) == 0.0  # exporting, nothing used from the grid
    flipped = LoadSources(grid="g", grid_flip=True)
    assert house_power_kw(flipped, reader(g=-0.8)) == pytest.approx(0.8)


def test_import_export_pair():
    src = LoadSources(grid_import="i", grid_export="e")
    assert house_power_kw(src, reader(i=1.5, e=0.0)) == pytest.approx(1.5)
    assert house_power_kw(src, reader(i=0.0, e=2.0)) == 0.0
    assert house_power_kw(src, reader(i=1.0)) is None  # one half missing: no guess


def test_solar_and_battery_are_part_of_the_picture():
    src = LoadSources(grid="g", solar="s", battery="b")
    # Exporting 1 kW, solar makes 3 kW, the battery takes 0.5 kW: the house uses 1.5 kW.
    assert house_power_kw(src, reader(g=-1.0, s=3.0, b=0.5)) == pytest.approx(1.5)
    # Importing 2 kW, 1.5 kW of it goes into the battery: the house uses 0.5 kW.
    assert house_power_kw(src, reader(g=2.0, s=0.0, b=1.5)) == pytest.approx(0.5)
    # Discharging 1 kW (negative) adds to what the house can use.
    assert house_power_kw(src, reader(g=0.0, s=0.0, b=-1.0)) == pytest.approx(1.0)
    flipped = LoadSources(grid="g", battery="b", battery_flip=True)
    assert house_power_kw(flipped, reader(g=0.0, b=1.0)) == pytest.approx(1.0)


def test_a_missing_sensor_means_no_reading():
    src = LoadSources(grid="g", solar="s")
    assert house_power_kw(src, reader(g=1.0)) is None
    assert house_power_kw(LoadSources(), reader()) is None


def test_configured_and_entities():
    assert not LoadSources().configured
    assert not LoadSources(solar="s").configured
    assert LoadSources(grid_import="i", grid_export="e").configured
    assert LoadSources(grid="g", solar="g").entities == ["g"]


def test_an_ev_charger_is_left_out():
    src = LoadSources(grid="g", ev="ev")
    assert house_power_kw(src, reader(g=8.0, ev=7.4)) == pytest.approx(0.6)  # charging at home
    assert house_power_kw(src, reader(g=1.0, ev=3.0)) == 0.0  # never negative
    assert house_power_kw(src, reader(g=1.0)) is None  # the charger's value is needed
    direct = LoadSources(house="h", ev="ev")
    assert house_power_kw(direct, reader(h=5.0, ev=4.0)) == pytest.approx(1.0)


# -- hourly history -------------------------------------------------------------


def ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def hours(start: str, count: int, kw_for) -> dict[int, float]:
    first = int(ts(start))
    return {first + i * HOUR: kw_for(datetime.fromtimestamp(first + i * HOUR, timezone.utc).astimezone(TZ)) for i in range(count)}


def test_hourly_values_combine_like_the_live_ones():
    src = LoadSources(grid="g", solar="s", battery="b", ev="e")
    first = int(ts("2026-11-02T10:00:00+02:00"))
    means = {
        "g": {first: 1.0, first + HOUR: 2.0},
        "s": {first: 3.0, first + HOUR: 0.0},
        "b": {first: 0.5, first + HOUR: 0.5},
        "e": {first: 1.0},  # no value for the second hour
    }
    assert hourly_kwh(src, means) == {first: pytest.approx(2.5)}  # 1 + 3 - 0.5 - 1


def test_the_mean_of_the_pair_is_the_difference_of_means():
    src = LoadSources(grid_import="i", grid_export="e")
    first = int(ts("2026-11-02T10:00:00+02:00"))
    assert hourly_kwh(src, {"i": {first: 1.5}, "e": {first: 0.5}}) == {first: pytest.approx(1.0)}


def test_forecast_separates_weekdays_and_weekends():
    def load(local: datetime) -> float:
        base = 4.0 if local.hour == 18 else 0.4  # a dinner peak
        return base * (2 if local.weekday() >= 5 else 1)  # weekends use more

    profile = LoadProfile(hours("2026-10-12T00:00:00+02:00", 24 * 21, load))  # three weeks
    now = ts("2026-11-02T12:00:00+02:00")
    weekday = profile.expected(datetime(2026, 11, 3, 18, 0, tzinfo=TZ), now, TZ)  # a Tuesday
    weekend = profile.expected(datetime(2026, 11, 7, 18, 0, tzinfo=TZ), now, TZ)  # a Saturday
    night = profile.expected(datetime(2026, 11, 3, 3, 0, tzinfo=TZ), now, TZ)
    assert weekday == pytest.approx(4.0) and weekend == pytest.approx(8.0)
    assert night == pytest.approx(0.4)
    assert profile.days_learned() == 21.0


def test_weekend_falls_back_to_weekday_history():
    profile = LoadProfile(hours("2026-11-02T00:00:00+02:00", 24, lambda _: 1.2))  # one Monday
    now = ts("2026-11-03T08:00:00+02:00")
    assert profile.expected(datetime(2026, 11, 7, 12, 0, tzinfo=TZ), now, TZ) == pytest.approx(1.2)


def test_no_history_no_forecast():
    assert not LoadProfile()
    assert LoadProfile().expected(datetime(2026, 11, 7, 12, 0, tzinfo=TZ), ts("2026-11-03T08:00:00+02:00"), TZ) is None


def test_old_history_is_not_used():
    profile = LoadProfile(hours("2026-08-01T00:00:00+02:00", 24, lambda _: 1.0))
    assert profile.expected(datetime(2026, 11, 3, 12, 0, tzinfo=TZ), ts("2026-11-02T12:00:00+02:00"), TZ) is None


def test_the_forecast_runs_from_the_hour_in_progress():
    profile = LoadProfile(hours("2026-11-02T00:00:00+02:00", 24, lambda _: 1.0))
    now = datetime(2026, 11, 3, 10, 40, tzinfo=TZ)
    rows = profile.forecast(now, 5, now.timestamp(), TZ)
    assert len(rows) == 5 and rows[1][0] - rows[0][0] == timedelta(hours=1)
    assert rows[0][0].astimezone(TZ).hour == 10


def test_several_solar_sensors_are_added_up():
    src = LoadSources(grid="g", solar=("s1", "s2", "s3"))
    # Exporting 1 kW with 2 + 1.5 + 0.5 kW of sun: the house uses 3 kW.
    assert house_power_kw(src, reader(g=-1.0, s1=2.0, s2=1.5, s3=0.5)) == pytest.approx(3.0)
    assert house_power_kw(src, reader(g=-1.0, s1=2.0, s2=1.5)) is None  # one is missing: no guess
    assert LoadSources(grid="g", solar=["s1", "s2"]).entities == ["g", "s1", "s2"]
    assert LoadSources(grid="g", solar="s1").solar_entities == ("s1",)
