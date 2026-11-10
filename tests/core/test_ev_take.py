"""The sun a car that follows the surplus is expected to take."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_planner.coordinator import PlannerCoordinator
from custom_components.battery_planner.prices import PricePoint
from custom_components.battery_planner.settings import EvSurplus

CAR = EvSurplus(start_kw=4.6, max_kw=11.0, battery_kw=1.0)
NOW = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def quarter_hours(count, start=NOW):
    return [PricePoint(start + timedelta(minutes=15 * i), start + timedelta(minutes=15 * (i + 1)), 1.0, 1.0) for i in range(count)]


def take(sun_kw, house_kw=0.8, probability=0.5, live=None, count=1, car=CAR):
    pts = quarter_hours(count)
    production = [sun_kw * 0.25] * count
    loads = [house_kw * 0.25] * count
    return PlannerCoordinator._ev_take(production, loads, 0.25, pts, NOW, car, probability, live)


def test_above_the_threshold_it_takes_the_surplus_less_the_batterys_share():
    # 6.8 kW of sun, 0.8 house: 6 kW of surplus, 5 after the battery's 1 kW, half the time.
    assert take(6.8) == pytest.approx([0.5 * 5.0 * 0.25])


def test_below_the_threshold_it_takes_nothing():
    assert take(5.0) == [0.0]  # 4.2 kW of surplus, the charger needs 4.6


def test_it_never_takes_more_than_its_maximum():
    assert take(30.0, probability=1.0) == pytest.approx([11.0 * 0.25])


def test_it_takes_in_proportion_to_how_often_it_charged():
    assert take(6.8, probability=0.0) == [0.0]
    assert take(6.8, probability=1.0) == pytest.approx([5.0 * 0.25])


def test_a_car_charging_now_carries_on_and_then_fades_into_the_usual():
    pts = take(6.8, probability=0.2, live=4.0, count=16)
    assert pts[0] == pytest.approx(4.0 * 0.25)  # what it draws now
    assert pts[-1] == pytest.approx(0.2 * 5.0 * 0.25, abs=0.08)  # almost the usual again after four hours
    assert pts[0] > pts[4] > pts[-1] - 1e-9


def test_a_car_charging_now_cannot_take_more_than_there_is():
    assert take(3.0, probability=0.5, live=8.0) == pytest.approx([(3.0 - 0.8 - 1.0) * 0.25])


def test_a_car_that_is_not_charging_now_changes_nothing():
    assert take(6.8, live=0.3) == take(6.8)
