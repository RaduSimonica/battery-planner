"""The three planning modes: house load, what may be exported, and the value of what is left."""

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_planner.arbitrage import (
    BALANCED,
    MAX_PROFIT,
    SELF_CONSUMPTION,
    BatteryParams,
    Slot,
    plan,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
BATTERY = BatteryParams(10, 5, 5, 0.9, 0.1)


def day(load_kwh=0.25, cheap=0.3, dear=2.0, mid=0.9, buy_markup=1.2):
    """A day in quarter hours: cheap at night, dear in the evening, a steady house load."""
    out = []
    for i in range(96):
        hour = i // 4
        price = cheap if 2 <= hour < 5 else dear if 18 <= hour < 21 else mid
        out.append(Slot(T0 + timedelta(minutes=15 * i), price * buy_markup, price, load_kwh=load_kwh))
    return out


def run(mode, slots=None, initial=0.0, terminal=0.0, battery=BATTERY):
    return plan(slots or day(), 0.25, battery, initial, mode=mode, terminal_value=terminal)


def test_max_profit_ignores_the_house():
    a = run(MAX_PROFIT, day(load_kwh=0.0))
    b = run(MAX_PROFIT, day(load_kwh=2.0), terminal=5.0)
    assert (a.net_gain, a.energy_sold_kwh, a.energy_to_house_kwh) == (b.net_gain, b.energy_sold_kwh, 0.0)
    assert a.mode == MAX_PROFIT and a.stored_value == 0.0


def test_self_consumption_never_exports_and_never_exceeds_the_load():
    p = run(SELF_CONSUMPTION)
    assert p.profitable
    assert p.energy_sold_kwh == pytest.approx(0.0, abs=1e-9)
    assert p.energy_to_house_kwh > 0
    for s in p.slots:
        if s.action == "discharge":
            assert s.grid_kwh <= 0.25 + 1e-9  # the house uses 0.25 kWh a quarter hour
            assert s.to_house_kwh == pytest.approx(s.grid_kwh)


def test_self_consumption_charges_in_the_cheap_hours_and_discharges_when_dear():
    p = run(SELF_CONSUMPTION)
    charges = [dt_hour(s.start) for s in p.slots if s.action == "charge"]
    discharges = [dt_hour(s.start) for s in p.slots if s.action == "discharge"]
    assert charges and all(2 <= h < 5 for h in charges)
    # Every dear evening quarter hour is covered from the battery, and none of the cheap ones.
    assert {18, 19, 20} <= set(discharges) and not any(2 <= h < 5 for h in discharges)


def dt_hour(moment: datetime) -> int:
    return moment.hour


def test_balanced_covers_the_house_first_then_exports():
    p = run(BALANCED)
    assert p.profitable and p.energy_to_house_kwh > 0 and p.energy_sold_kwh > 0
    # Covering the house is worth the buy price, so it is done in every discharging slot.
    for s in p.slots:
        if s.action == "discharge":
            assert s.to_house_kwh == pytest.approx(min(s.grid_kwh, 0.25), abs=1e-6)


def test_balanced_is_never_worse_than_self_consumption():
    balanced = run(BALANCED, terminal=0.4)
    only_house = run(SELF_CONSUMPTION, terminal=0.4)
    assert balanced.net_gain + balanced.stored_value >= only_house.net_gain + only_house.stored_value - 1e-6
    assert balanced.energy_sold_kwh >= only_house.energy_sold_kwh


def test_the_accounts_add_up():
    for mode in (MAX_PROFIT, SELF_CONSUMPTION, BALANCED):
        p = run(mode, terminal=0.4)
        assert p.net_gain == pytest.approx(p.earnings - p.expenses - p.degradation_cost)
        assert p.energy_bought_kwh >= 0 and p.expenses >= 0 and p.earnings >= 0


def test_what_is_left_in_the_battery_has_a_value():
    # Without a value for what is left, everything is sold before the day ends.
    sells_all = run(BALANCED, terminal=0.0)
    # Worth more than anything it could earn today: it is bought up to a full battery and kept.
    keeps = run(BALANCED, terminal=3.0)
    assert sells_all.energy_sold_kwh > 0 and sells_all.soc[-1] < 1.0
    assert keeps.soc[-1] > 9.0  # the battery ends the day full, for tomorrow's house
    assert keeps.stored_value > 0


def test_nothing_is_exported_below_the_value_of_what_is_left():
    # A flat dear evening: nothing cheap to refill with, and a stored kWh is worth more than a sale.
    evening = [Slot(T0 + timedelta(minutes=15 * i), 2.4, 2.0, load_kwh=0.25) for i in range(24)]
    p = run(BALANCED, evening, initial=10.0, terminal=3.0)
    assert p.energy_sold_kwh == pytest.approx(0.0, abs=1e-9)
    assert p.soc[-1] > 9.0


def test_no_load_means_nothing_to_cover():
    p = run(SELF_CONSUMPTION, day(load_kwh=0.0))
    assert not p.profitable and p.slots == []


def test_a_larger_load_is_covered_in_more_slots():
    small = run(SELF_CONSUMPTION, day(load_kwh=0.1))
    big = run(SELF_CONSUMPTION, day(load_kwh=0.5))
    assert big.energy_to_house_kwh > small.energy_to_house_kwh


def test_the_reserve_still_holds_in_every_mode():
    battery = BatteryParams(10, 5, 5, 0.9, 0.1, min_kwh=5.0)
    for mode in (SELF_CONSUMPTION, BALANCED):
        p = run(mode, initial=10.0, terminal=0.4, battery=battery)
        assert min(p.soc) >= 5.0 - 1e-6


def test_energy_balance_per_slot():
    p = run(BALANCED, initial=4.0, terminal=0.4)
    level = p.soc[0]
    eff = BATTERY.round_trip_efficiency ** 0.5
    starts = {s.start: s for s in p.slots}
    for i in range(96):
        s = starts.get(T0 + timedelta(minutes=15 * i))
        if s is None:
            expected = level
        elif s.action == "charge":
            expected = level + s.grid_kwh * eff
        else:
            expected = level - s.grid_kwh / eff
        assert p.soc[i + 1] == pytest.approx(expected, abs=0.06)
        level = p.soc[i + 1]


def test_running_totals_follow_the_plan():
    p = run(BALANCED, terminal=0.4)
    n = 96
    assert len(p.earnings_curve) == len(p.expenses_curve) == len(p.net_curve) == len(p.soc) == n + 1
    assert p.earnings_curve[0] == p.expenses_curve[0] == p.net_curve[0] == 0.0
    assert p.earnings_curve[-1] == pytest.approx(p.earnings)
    assert p.expenses_curve[-1] == pytest.approx(p.expenses)
    assert p.net_curve[-1] == pytest.approx(p.net_gain)
    assert all(b >= a - 1e-9 for a, b in zip(p.earnings_curve, p.earnings_curve[1:]))  # only grows
    assert all(b >= a - 1e-9 for a, b in zip(p.expenses_curve, p.expenses_curve[1:]))
