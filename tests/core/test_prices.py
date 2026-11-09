"""Reading the price series contract."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_planner.prices import PriceSeries, currency_of, parse_prices

ROW = {"start": "2026-11-09T12:00:00+02:00", "end": "2026-11-09T12:15:00+02:00", "buy": 1.2, "sell": 0.9}


def test_parses_a_valid_row_to_utc():
    (p,) = parse_prices([ROW])
    assert p.start == datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
    assert p.end - p.start == timedelta(minutes=15) and (p.buy, p.sell) == (1.2, 0.9)


def test_extra_keys_and_optional_spot_are_fine():
    assert len(parse_prices([ROW | {"spot": 0.9, "future_field": [1, 2]}])) == 1


def test_negative_and_zero_prices_are_valid():
    (p,) = parse_prices([ROW | {"buy": -0.1, "sell": 0}])
    assert (p.buy, p.sell) == (-0.1, 0.0)


@pytest.mark.parametrize(
    "bad",
    [
        {**ROW, "start": "2026-11-09T12:00:00"},  # no UTC offset: cannot be placed
        {**ROW, "start": "yesterday"},
        {**ROW, "start": None},
        {**ROW, "end": ROW["start"]},  # empty interval
        {**ROW, "end": "2026-11-09T11:00:00+02:00"},  # ends before it starts
        {**ROW, "buy": "1.2"},
        {**ROW, "buy": None},
        {**ROW, "sell": True},
        {**ROW, "sell": float("nan")},
        {**ROW, "buy": float("inf")},
        {k: v for k, v in ROW.items() if k != "sell"},
        "not a row",
        None,
    ],
)
def test_bad_rows_are_skipped_not_fatal(bad):
    assert [p.buy for p in parse_prices([bad, ROW | {"buy": 7.0}])] == [7.0]


@pytest.mark.parametrize("raw", [None, "text", 5, {"a": 1}, []])
def test_attribute_that_is_not_a_list(raw):
    assert parse_prices(raw) == []


def test_rows_are_sorted():
    later = ROW | {"start": "2026-11-09T13:00:00+02:00", "end": "2026-11-09T13:15:00+02:00", "buy": 5.0}
    assert [p.buy for p in parse_prices([later, ROW])] == [1.2, 5.0]


@pytest.mark.parametrize(
    "unit, code",
    [("RON/kWh", "RON"), ("EUR/kWh", "EUR"), ("eur/kWh", None), ("lei/kWh", None), ("kWh", None),
     ("", None), (None, None), (5, None), ("cents/kWh", None)],
)
def test_currency_of(unit, code):
    assert currency_of(unit) == code


def test_series_lookup_and_windows():
    rows = [
        {**ROW, "start": f"2026-11-09T{h:02d}:00:00+02:00", "end": f"2026-11-09T{h + 1:02d}:00:00+02:00"}
        for h in range(10, 14)
    ]
    s = PriceSeries(parse_prices(rows))
    at = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)  # 10:30 local
    assert s.at(at).start == datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    assert s.at(at - timedelta(hours=2)) is None and s.at(datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)) is None
    start = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    assert len(s.window(start, start + timedelta(hours=2))) == 2
    assert len(s.window(at, at + timedelta(minutes=1))) == 1  # the interval in progress counts
    assert s.covers(start, start + timedelta(hours=4)) is not None
    assert s.covers(start - timedelta(hours=1), start + timedelta(hours=4)) is None
    assert s.covers(start, start + timedelta(hours=5)) is None
    assert not PriceSeries() and PriceSeries().at(at) is None
