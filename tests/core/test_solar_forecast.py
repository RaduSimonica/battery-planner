"""The solar forecast spread over intervals, and bent towards what the panels make now."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_planner import solar
from custom_components.battery_planner.prices import PricePoint

UTC = timezone.utc


def points(start="2026-06-01T10:00:00+00:00", count=8, minutes=15):
    t0 = datetime.fromisoformat(start)
    return [PricePoint(t0 + timedelta(minutes=minutes * i), t0 + timedelta(minutes=minutes * (i + 1)), 1.0, 1.0) for i in range(count)]


def hourly(**by_hour):
    """Start-aligned hourly forecast (the Home Assistant convention) as intervals."""
    return solar.to_intervals(
        "fake", {datetime(2026, 6, 1, int(h[1:]), tzinfo=UTC).isoformat(): wh for h, wh in by_hour.items()}
    )


def test_an_hour_is_spread_over_its_quarters():
    energies = solar.slot_kwh(points(), hourly(h10=2000, h11=1000))
    assert energies == pytest.approx([0.5] * 4 + [0.25] * 4)
    assert sum(energies) == pytest.approx(3.0)


def test_intervals_across_two_periods_take_a_share_of_each():
    energies = solar.slot_kwh(points(minutes=30, count=2, start="2026-06-01T10:30:00+00:00"), hourly(h10=2000, h11=1000))
    assert energies == pytest.approx([1.0, 0.5])  # the second half of the first hour, then the first half of the next


def test_no_forecast_no_production():
    assert solar.slot_kwh(points(), {}) == [0.0] * 8


def test_half_hour_periods_are_understood():
    forecast = solar.to_intervals("fake", {"2026-06-01T10:00:00+00:00": 1000.0, "2026-06-01T10:30:00+00:00": 500.0})
    assert solar.slot_kwh(points(count=4), forecast) == pytest.approx([0.5, 0.5, 0.25, 0.25])


def test_a_cloudier_moment_scales_the_next_hour_down_and_fades():
    pts = points(count=16)
    base = solar.slot_kwh(pts, hourly(h10=2000, h11=2000, h12=2000, h13=2000))
    now = pts[0].start
    bent = solar.follow_live(base, pts, 0.25, now, live_kw=1.0)  # half of the 2 kW forecast
    assert bent[0] == pytest.approx(base[0] * 0.5)
    assert base[0] > bent[1] >= bent[0]  # recovering
    assert bent[-1] > base[-1] * 0.9  # almost forecast again three hours on


def test_a_sunnier_moment_is_capped_at_double():
    pts = points(count=4)
    base = solar.slot_kwh(pts, hourly(h10=1000))
    assert solar.follow_live(base, pts, 0.25, pts[0].start, live_kw=10.0)[0] == pytest.approx(base[0] * 2)


def test_sun_before_the_forecast_expects_any_is_added():
    pts = points(start="2026-06-01T04:00:00+00:00", count=4)
    base = solar.slot_kwh(pts, hourly(h4=0, h5=800))
    bent = solar.follow_live(base, pts, 0.25, pts[0].start, live_kw=0.6)
    assert bent[0] == pytest.approx(0.6 * 0.25)


def test_without_a_live_reading_the_forecast_stands():
    pts = points()
    base = solar.slot_kwh(pts, hourly(h10=2000))
    assert solar.follow_live(base, pts, 0.25, pts[0].start, None) == base


# The real thing: Forecast.Solar's answer for one array on 10 November 2026 (Romania).
# Its stamps mark the END of each period, with extra stamps at sunrise and sunset.
REAL = {
    "2026-11-10T05:05:45+00:00": 0, "2026-11-10T06:00:00+00:00": 71, "2026-11-10T07:00:00+00:00": 216,
    "2026-11-10T08:00:00+00:00": 321, "2026-11-10T09:00:00+00:00": 433, "2026-11-10T10:00:00+00:00": 547,
    "2026-11-10T11:00:00+00:00": 539, "2026-11-10T12:00:00+00:00": 435, "2026-11-10T13:00:00+00:00": 316,
    "2026-11-10T14:00:00+00:00": 172, "2026-11-10T14:53:57+00:00": 44,
    "2026-11-11T05:07:06+00:00": 0, "2026-11-11T06:00:00+00:00": 69,
}


def quarter_hours(start_iso, count):
    t0 = datetime.fromisoformat(start_iso)
    return [PricePoint(t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)), 1.0, 1.0) for i in range(count)]


def test_forecast_solar_stamps_mark_the_end_of_a_period():
    intervals = solar.to_intervals("forecast_solar", REAL)
    day = quarter_hours("2026-11-10T00:00:00+00:00", 96)
    energies = solar.slot_kwh(day, intervals)
    assert sum(energies) == pytest.approx(sum(v for k, v in REAL.items() if k.startswith("2026-11-10")) / 1000)  # nothing lost
    hour = lambda h, q: energies[h * 4 + q]  # noqa: E731
    # 547 Wh is the hour BEFORE the 10:00 stamp, so 09:30 has a quarter of it (not 10:30).
    assert hour(9, 2) == pytest.approx(0.547 / 4)
    assert hour(10, 2) == pytest.approx(0.539 / 4)  # and the next hour is 539
    assert hour(4, 0) == 0.0  # dark before sunrise (05:05)
    assert hour(14, 3) == pytest.approx(0.044 / 3.9, abs=0.02)  # the sliver up to sunset at 14:53:57
    assert energies[15 * 4] == 0.0  # dark after sunset


def test_the_same_data_read_as_start_aligned_is_an_hour_late():
    end_aligned = solar.slot_kwh(quarter_hours("2026-11-10T09:30:00+00:00", 1), solar.to_intervals("forecast_solar", REAL))[0]
    start_aligned = solar.slot_kwh(quarter_hours("2026-11-10T09:30:00+00:00", 1), solar.to_intervals("solcast", REAL))[0]
    assert end_aligned > start_aligned  # the peak hours straddle solar noon only when read as the end


def test_a_night_gap_is_not_a_period():
    intervals = solar.to_intervals("forecast_solar", REAL)
    night = solar.slot_kwh(quarter_hours("2026-11-10T20:00:00+00:00", 4), intervals)
    assert night == [0.0] * 4
    first_next = solar.slot_kwh(quarter_hours("2026-11-11T05:30:00+00:00", 2), intervals)
    assert first_next[0] > 0  # the 69 Wh up to 06:00 starts at the 05:07 stamp
