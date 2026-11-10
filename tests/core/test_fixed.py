"""Fixed prices: quarter hours priced by their local time."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.battery_planner import fixed
from custom_components.battery_planner.prices import local_day_bounds

TZ = ZoneInfo("Europe/Bucharest")
FLAT = fixed.FixedPrices(1.5, 0.3)


def local(text):
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def day(prices, iso):
    start, end = local_day_bounds(datetime.fromisoformat(iso).date(), TZ)
    return list(fixed.points(prices, start, end, TZ))


def test_a_flat_day_is_quarter_hours_of_one_price():
    pts = day(FLAT, "2026-11-10")
    assert len(pts) == 96 and {(p.buy, p.sell) for p in pts} == {(1.5, 0.3)}
    assert all(b.start == a.end for a, b in zip(pts, pts[1:]))  # no gaps


@pytest.mark.parametrize("iso, count", [("2026-03-29", 92), ("2026-10-25", 100)])
def test_daylight_saving_days_have_the_right_number_of_intervals(iso, count):
    assert len(day(FLAT, iso)) == count


def test_a_night_rate_runs_past_midnight():
    prices = fixed.FixedPrices(1.5, 0.3, fixed.SecondRate(time(22), time(6), 0.6, 0.2))
    rate = lambda t: prices.rate(local(t))  # noqa: E731
    assert rate("2026-11-10T23:00") == (0.6, 0.2) and rate("2026-11-11T05:45") == (0.6, 0.2)
    assert rate("2026-11-11T06:00") == (1.5, 0.3) and rate("2026-11-10T21:45") == (1.5, 0.3)
    pts = day(prices, "2026-11-11")
    night = [p for p in pts if p.buy == 0.6]
    assert len(night) == 8 * 4 - 0  # 00:00-06:00 and 22:00-24:00


def test_a_daytime_rate_and_the_days_it_applies_to():
    midday = lambda days: fixed.FixedPrices(1.5, 0.3, fixed.SecondRate(time(12), time(14), 0.9, 0.9, days))  # noqa: E731
    saturday, tuesday = local("2026-11-14T13:00"), local("2026-11-10T13:00")
    assert midday("every").rate(saturday) == midday("every").rate(tuesday) == (0.9, 0.9)
    assert midday("weekends").rate(saturday) == (0.9, 0.9) and midday("weekends").rate(tuesday) == (1.5, 0.3)
    assert midday("weekdays").rate(saturday) == (1.5, 0.3) and midday("weekdays").rate(tuesday) == (0.9, 0.9)
    assert midday("every").rate(local("2026-11-10T14:00")) == (1.5, 0.3)  # the end is not included


def test_settings_become_prices():
    data = {
        "fixed_buy_price": "1.5", "fixed_sell_price": 0.3, "rate2_from": "22:00:00", "rate2_to": "06:00:00",
        "rate2_buy_price": 0.6, "rate2_sell_price": 0.2, "rate2_days": "weekends",
    }
    prices = fixed.from_data(data)
    assert (prices.buy, prices.sell) == (1.5, 0.3)
    assert prices.second == fixed.SecondRate(time(22), time(6), 0.6, 0.2, "weekends")


@pytest.mark.parametrize(
    "broken",
    [{}, {"fixed_buy_price": 1.5}, {"fixed_buy_price": "x", "fixed_sell_price": 0.3}],
)
def test_without_usable_prices_there_are_none(broken):
    assert fixed.from_data(broken) is None


@pytest.mark.parametrize(
    "extra",
    [
        {"rate2_from": "22:00", "rate2_to": "22:00", "rate2_buy_price": 1, "rate2_sell_price": 1},  # no time at all
        {"rate2_from": "bad", "rate2_to": "06:00", "rate2_buy_price": 1, "rate2_sell_price": 1},
        {"rate2_from": "22:00", "rate2_to": "06:00", "rate2_buy_price": 1},  # no sell price
    ],
)
def test_a_second_rate_that_cannot_work_is_ignored(extra):
    prices = fixed.from_data({"fixed_buy_price": 1.5, "fixed_sell_price": 0.3, **extra})
    assert prices is not None and prices.second is None
