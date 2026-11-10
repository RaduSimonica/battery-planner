"""The planner (ported from OPCOM PZU, where it was developed) and its invariants."""

import random
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_planner import settings
from custom_components.battery_planner.arbitrage import BatteryParams, Slot, plan
from custom_components.battery_planner.sensor import _blocks

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
STEP = timedelta(minutes=15)


def _slots(prices):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Slot(t0 + timedelta(minutes=15 * i), p, p) for i, p in enumerate(prices)]


def test_plan_profitable_and_ordering():
    prices = [0.3] * 8 + [1.5] * 8
    p = plan(_slots(prices), 0.25, BatteryParams(10, 10, 10, 0.9, 0.1))
    assert p.profitable
    charges = [s.start for s in p.slots if s.action == "charge"]
    discharges = [s.start for s in p.slots if s.action == "discharge"]
    assert max(charges) < min(discharges)
    assert p.energy_sold_kwh <= 10  # never more than usable capacity
    assert p.gain_per_kwh_sold == pytest.approx(p.net_gain / p.energy_sold_kwh)
    # 1 kWh sold costs 0.3/0.9 + 0.1 degradation, so net per kWh ~ 1.067
    assert p.gain_per_kwh_sold == pytest.approx(1.5 - 0.1 - 0.3 / 0.9, abs=0.02)


def test_plan_not_profitable_when_spread_too_small():
    p = plan(_slots([0.50] * 8 + [0.52] * 8), 0.25, BatteryParams(10, 10, 10, 0.9, 0.1))
    assert not p.profitable and p.net_gain == 0 and p.slots == []


def test_plan_respects_power_limit():
    p = plan(_slots([0.1] * 4 + [2.0] * 4), 0.25, BatteryParams(100, 4, 4, 1.0, 0))
    assert p.energy_sold_kwh <= 4 * 0.25 * 4 + 1e-9  # 4 kW for 4 slots of 15 min


def test_plan_multiple_cycles():
    prices = [0.2] * 4 + [1.5] * 4 + [0.2] * 4 + [1.5] * 4
    p = plan(_slots(prices), 0.25, BatteryParams(2, 8, 8, 1.0, 0))
    assert p.cycles == pytest.approx(2, abs=0.1)


def test_plan_sells_energy_already_in_battery():
    # Buying is never worth it (3.0 in), but 5 kWh are stored and the evening pays 2.0.
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sell = [0.5] * 8 + [2.0] * 4
    slots = [Slot(t0 + timedelta(minutes=15 * i), 3.0, p) for i, p in enumerate(sell)]
    p = plan(slots, 0.25, BatteryParams(10, 10, 10, 0.9, 0.1), initial_kwh=5)
    assert p.profitable and p.energy_bought_kwh == 0
    assert 4 < p.energy_sold_kwh <= 5  # most of the stored energy goes out, none is invented
    assert p.gain_per_kwh_bought is None and p.break_even_sell_price is None
    assert p.gain_per_kwh_sold == pytest.approx(2.0 - 0.1, abs=0.02)  # no purchase cost


def test_plan_full_battery_cannot_charge_more():
    p = plan(_slots([0.1] * 4 + [2.0] * 4), 0.25, BatteryParams(4, 20, 20, 1.0, 0), initial_kwh=4)
    assert p.energy_bought_kwh == 0 and p.energy_sold_kwh == pytest.approx(4, abs=0.3)


def test_plan_initial_energy_never_hurts():
    prices = [0.3] * 6 + [1.4] * 6
    base = plan(_slots(prices), 0.25, BatteryParams(10, 10, 10, 0.9, 0.1))
    more = plan(_slots(prices), 0.25, BatteryParams(10, 10, 10, 0.9, 0.1), initial_kwh=3)
    assert more.net_gain >= base.net_gain - 1e-9


def test_plan_different_charge_and_discharge_power():
    # Slow 0.3 kW charge, fast 1.2 kW discharge: a day of cheap hours, a short dear peak.
    prices = [0.3] * 64 + [2.0] * 8 + [0.3] * 24
    p = plan(_slots(prices), 0.25, BatteryParams(10, 0.3, 1.2, 0.9, 0.1))
    assert p.profitable
    for slot in p.slots:
        limit = 0.3 * 0.25 if slot.action == "charge" else 1.2 * 0.25
        assert slot.grid_kwh <= limit + 1e-9  # each direction keeps to its own limit
    assert max(s.grid_kwh for s in p.slots if s.action == "discharge") > 0.3 * 0.25  # faster out than in
    assert p.energy_sold_kwh <= 1.2 * 0.25 * 8 + 1e-9  # the peak is only 8 slots long


def test_plan_asymmetry_the_other_way():
    prices = [0.3] * 8 + [2.0] * 64 + [0.3] * 24
    p = plan(_slots(prices), 0.25, BatteryParams(10, 1.2, 0.3, 0.9, 0.1))
    assert p.profitable
    assert all(s.grid_kwh <= 1.2 * 0.25 + 1e-9 for s in p.slots if s.action == "charge")
    assert all(s.grid_kwh <= 0.3 * 0.25 + 1e-9 for s in p.slots if s.action == "discharge")




def test_stored_energy_can_be_sold_in_the_last_interval():
    p = plan([Slot(datetime(2026, 1, 1, tzinfo=timezone.utc), 0.5, 3.0)], 0.25, BatteryParams(10, 5, 5), initial_kwh=5)
    assert p.profitable and p.slots[0].action == "discharge"
    # Without stored energy one interval is not enough to do anything.
    assert not plan([Slot(datetime(2026, 1, 1, tzinfo=timezone.utc), 0.5, 3.0)], 0.25, BatteryParams()).profitable


def test_partial_interval_limits_power():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    params = BatteryParams(10, 4, 4, 1.0, 0.0)  # 1 kWh per full 15 min interval
    full = plan([Slot(t0, 3.0, 3.0), Slot(t0 + timedelta(minutes=15), 3.0, 3.0)], 0.25, params, 4.0)
    half = plan([Slot(t0, 3.0, 3.0, fraction=0.5), Slot(t0 + timedelta(minutes=15), 3.0, 3.0)], 0.25, params, 4.0)
    assert half.energy_sold_kwh < full.energy_sold_kwh  # the planner's grain is coarse, so no exact kWh
    assert half.energy_sold_kwh <= 1.5 + 1e-9 and full.energy_sold_kwh > 1.5




def slots(prices, sell=None):
    sell = sell or prices
    return [Slot(T0 + STEP * i, b, s) for i, (b, s) in enumerate(zip(prices, sell))]


# -- planner invariants --------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
def test_plan_invariants_on_random_days(seed):
    rnd = random.Random(seed)
    params = BatteryParams(
        capacity_kwh=rnd.choice([2, 5, 10, 40]),
        max_charge_kw=rnd.choice([0.3, 1.2, 5, 12]),
        max_discharge_kw=rnd.choice([0.3, 1.2, 5, 12]),
        round_trip_efficiency=rnd.choice([0.7, 0.9, 1.0]),
        degradation_per_kwh=rnd.choice([0, 0.1, 0.4]),
    )
    prices = [rnd.uniform(-0.2, 2.5) for _ in range(rnd.choice([12, 48, 96]))]
    initial = rnd.choice([0.0, params.capacity_kwh / 2])
    p = plan(slots(prices), 0.25, params, initial)

    assert p.net_gain >= 0  # doing nothing is always possible
    assert p.profitable == (p.net_gain > 0)
    charge = [s for s in p.slots if s.action == "charge"]
    discharge = [s for s in p.slots if s.action == "discharge"]
    # Power limits (grid side) hold in every slot.
    assert all(s.grid_kwh <= params.max_charge_kw * 0.25 + 1e-6 for s in charge)
    assert all(s.grid_kwh <= params.max_discharge_kw * 0.25 + 1e-6 for s in discharge)
    # Energy balance: what is sold comes from what was stored.
    one_way = params.round_trip_efficiency**0.5
    stored_in = sum(s.grid_kwh for s in charge) * one_way + initial
    # (the planner rounds the starting energy to its grain, at most capacity/20)
    assert sum(s.grid_kwh for s in discharge) / one_way <= stored_in + params.capacity_kwh / 20 + 1e-6
    # Replaying the schedule never leaves the battery below empty or above full.
    level = initial
    for s in p.slots:
        level += s.grid_kwh * one_way if s.action == "charge" else -s.grid_kwh / one_way
        assert -params.capacity_kwh / 20 - 1e-6 <= level <= params.capacity_kwh * 1.05 + 1e-6  # grain


def test_flat_prices_never_trade():
    assert not plan(slots([1.0] * 96), 0.25, BatteryParams()).profitable


def test_a_dearer_battery_never_gains_more():
    prices = [0.3] * 8 + [2.0] * 8 + [0.4] * 8 + [2.2] * 8
    gains = [plan(slots(prices), 0.25, BatteryParams(degradation_per_kwh=d)).net_gain for d in (0, 0.2, 0.6, 5)]
    assert gains == sorted(gains, reverse=True) and gains[-1] == 0


def test_better_efficiency_never_gains_less():
    prices = [0.3] * 8 + [1.5] * 8
    gains = [plan(slots(prices), 0.25, BatteryParams(round_trip_efficiency=e)).net_gain for e in (0.6, 0.8, 1.0)]
    assert gains == sorted(gains)


def test_negative_buy_price_pays_to_charge():
    p = plan(slots([-1.0, -1.0, 0.5, 0.5], [-1.0, -1.0, 0.5, 0.5]), 0.25, BatteryParams(degradation_per_kwh=0))
    assert p.profitable and p.slots[0].action == "charge"


def test_own_buy_and_sell_prices_are_used():
    # Cheap to buy, but the sell price is always below the break-even: no trade.
    assert not plan(slots([0.1] * 4 + [0.1] * 4, [0.05] * 8), 0.25, BatteryParams()).profitable


def test_empty_and_degenerate_inputs():
    assert not plan([], 0.25, BatteryParams()).profitable
    assert not plan(slots([1, 2]), 0.25, BatteryParams(capacity_kwh=0)).profitable
    assert not plan(slots([1, 2]), 0.25, BatteryParams(max_charge_kw=0)).profitable
    assert not plan(slots([1, 2]), 0.25, BatteryParams(max_discharge_kw=0)).profitable


def test_slot_with_no_time_left_does_nothing():
    cheap_then_dear = [Slot(T0, 0.1, 0.1, fraction=0.0), Slot(T0 + STEP, 3.0, 3.0)]
    assert not plan(cheap_then_dear, 0.25, BatteryParams()).profitable


def test_hourly_resolution():
    prices = [0.3, 0.3, 2.0, 2.0]
    p = plan([Slot(T0 + timedelta(hours=i), x, x) for i, x in enumerate(prices)], 1.0, BatteryParams())
    assert p.profitable and p.slots[0].start == T0


# -- schedule blocks -----------------------------------------------------------


def test_blocks_merge_neighbouring_slots():
    p = plan(slots([0.2] * 4 + [3.0] * 4), 0.25, BatteryParams(degradation_per_kwh=0))
    blocks = _blocks(p, STEP)
    assert [b["action"] for b in blocks] == ["charge", "discharge"]
    assert blocks[0]["from"] != blocks[0]["to"]
    assert blocks[0]["avg_price"] == pytest.approx(0.2) and blocks[1]["avg_price"] == pytest.approx(3.0)
    assert blocks[0]["kwh"] == pytest.approx(sum(s.grid_kwh for s in p.slots if s.action == "charge"), abs=0.01)


def test_blocks_split_when_not_contiguous():
    p = plan(slots([0.2, 3.0, 3.0, 0.2, 3.0]), 0.25, BatteryParams(capacity_kwh=1.2, degradation_per_kwh=0))
    actions = [b["action"] for b in _blocks(p, STEP)]
    assert actions.count("charge") >= 1 and "discharge" in actions


def test_settings_defaults_and_overrides():
    b = settings.battery({})
    assert (b.capacity_kwh, b.max_charge_kw, b.max_discharge_kw) == (10.0, 5.0, 5.0)
    assert b.round_trip_efficiency == pytest.approx(0.9)
    over = settings.battery({"battery_charge_power_kw": 0.3, "battery_discharge_power_kw": 1.2})
    assert (over.max_charge_kw, over.max_discharge_kw) == (0.3, 1.2)


def test_equal_schedules_never_churn():
    """Selling stored energy cheaply and buying it back is not 'equally good': it is nothing."""
    p = plan(slots([0.1] * 4 + [2.0] * 4), 0.25, BatteryParams(4, 20, 20, 1.0, 0), initial_kwh=4)
    assert p.energy_bought_kwh == 0 and all(s.action == "discharge" for s in p.slots)


# -- state-of-charge curve ----------------------------------------------------


def _day(prices):
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Slot(t0 + timedelta(hours=i), p, p) for i, p in enumerate(prices)]


def test_soc_curve_follows_the_plan():
    from math import sqrt

    import pytest

    params = BatteryParams(capacity_kwh=10, max_charge_kw=5, max_discharge_kw=5,
                           round_trip_efficiency=0.81, degradation_per_kwh=0.0)
    result = plan(_day([0.1, 0.1, 0.1, 2.0, 2.0, 2.0]), 1.0, params, initial_kwh=2.0)
    assert result.profitable and len(result.soc) == 7  # one more than the slots
    assert result.soc[0] == 2.0
    assert all(0 <= v <= 10 + 1e-9 for v in result.soc)
    one_way = sqrt(0.81)
    assert result.soc[-1] == pytest.approx(
        2.0 + result.energy_bought_kwh * one_way - result.energy_sold_kwh / one_way, abs=1e-6
    )
    peak = max(range(7), key=lambda i: result.soc[i])
    assert result.soc[peak] == max(result.soc) and peak in (3,)  # full just before the dear hours


def test_soc_curve_is_flat_when_nothing_pays():
    result = plan(_day([0.01, 0.01, 0.01, 0.01]), 1.0, BatteryParams(), initial_kwh=3.0)
    assert not result.profitable and result.soc == [3.0] * 5


# -- reserve (minimum level) ------------------------------------------------


def _reserve_params(**kw):
    return BatteryParams(capacity_kwh=10, max_charge_kw=5, max_discharge_kw=5,
                         round_trip_efficiency=1.0, degradation_per_kwh=0.0, **kw)


def test_reserve_is_never_discharged():
    result = plan(_day([0.1, 0.1, 0.1, 2.0, 2.0, 2.0]), 1.0, _reserve_params(min_kwh=5), initial_kwh=5.0)
    assert result.profitable
    assert min(result.soc) >= 5.0 - 1e-6
    assert result.energy_sold_kwh == pytest.approx(result.energy_bought_kwh, abs=1e-6)  # only what it bought


def test_a_battery_below_the_reserve_cannot_sell_but_can_charge():
    prices = [0.1, 0.1, 0.1, 2.0, 2.0, 2.0]
    result = plan(_day(prices), 1.0, _reserve_params(min_kwh=5), initial_kwh=2.0)
    # It starts low, may buy cheap, and may only sell what is above the reserve.
    assert result.soc[0] == 2.0
    for before, after in zip(result.soc, result.soc[1:]):
        if after < before:  # a discharge never ends below the reserve
            assert after >= 5.0 - 1e-6
    # The first 3 kWh bought only refill the reserve (2 -> 5), so they cannot be sold.
    assert result.energy_sold_kwh <= result.energy_bought_kwh - 3.0 + 1e-6


def test_a_battery_below_the_reserve_with_nothing_to_gain_stays_put():
    result = plan(_day([1.0, 1.0, 1.0, 1.0]), 1.0, _reserve_params(min_kwh=5), initial_kwh=2.0)
    assert not result.profitable and result.soc == [2.0] * 5


def test_no_reserve_behaves_as_before():
    a = plan(_day([0.1, 0.1, 2.0, 2.0]), 1.0, _reserve_params(min_kwh=0), initial_kwh=0.0)
    b = plan(_day([0.1, 0.1, 2.0, 2.0]), 1.0, _reserve_params(), initial_kwh=0.0)
    assert a.net_gain == b.net_gain and a.soc == b.soc
