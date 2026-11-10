"""Solar in the plan: free charging, not exporting cheap, the export cap and negative prices."""

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

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
BATTERY = BatteryParams(10, 5, 5, 0.9, 0.1)


def sunny_day(load=0.2, peak=1.2, sell=None, cap_hours=(9, 16)):
    """Quarter hours of a summer day. ``peak`` kWh of sun per quarter hour at noon, a steady load."""
    out = []
    for i in range(96):
        hour = i // 4
        sun = 0.0
        if cap_hours[0] <= hour < cap_hours[1]:
            sun = peak * (1 - abs(hour + 0.5 - 12.5) / 4)
        price = 0.2 if 10 <= hour < 15 else 2.0 if 19 <= hour < 22 else 0.9  # cheap at noon, dear in the evening
        if sell is not None and 10 <= hour < 15:
            price = sell
        buy = price * 1.2 if price >= 0 else 0.5  # a negative sell price still costs 0.5 to buy
        out.append(Slot(T0 + timedelta(minutes=15 * i), buy, price, load_kwh=load, solar_kwh=max(sun, 0.0)))
    return out


def run(mode=BALANCED, slots=None, initial=0.0, terminal=0.9, battery=BATTERY):
    return plan(slots or sunny_day(), 0.25, battery, initial, mode=mode, terminal_value=terminal)


def exported(p):
    return sum(f.grid_export_kwh for f in p.flows)


def test_the_sun_charges_the_battery_for_free():
    p = run()
    assert p.profitable
    assert p.energy_from_solar_kwh > 5.0  # a good part of the battery comes from the roof
    assert p.energy_bought_kwh < 1.0  # and almost none from the grid
    assert max(p.soc) > 8.0


def test_cheap_midday_surplus_is_kept_for_the_expensive_evening():
    p = run()
    base = run(slots=[Slot(s.start, s.buy_price, s.sell_price, s.fraction, s.load_kwh, s.solar_kwh) for s in sunny_day()],
               battery=BatteryParams(0.1, 5, 5, 0.9, 0.1))  # a battery too small to matter
    midday = lambda plan_, slots_: sum(  # noqa: E731
        f.grid_export_kwh for f, sl in zip(plan_.flows, slots_) if 10 <= sl.start.hour < 15
    )
    assert midday(p, sunny_day()) < midday(base, sunny_day()) - 5.0  # much less goes to the grid at noon
    evening = sum(s.grid_kwh for s in p.slots if s.action == "discharge" and 19 <= s.start.hour < 22)
    assert evening > 4.0  # and it comes out when the price is high
    assert p.net_gain > base.net_gain


def test_a_full_battery_exports_the_rest_anyway():
    p = run(battery=BatteryParams(2, 5, 5, 0.9, 0.1), terminal=0.9)  # 2 kWh fills up quickly
    assert exported(p) > 5.0
    assert p.curtailed_kwh == pytest.approx(0.0, abs=0.5)


def test_nothing_is_exported_at_a_negative_price():
    p = run(slots=sunny_day(sell=-0.4))
    noon = [f for f, s in zip(p.flows, sunny_day(sell=-0.4)) if 10 <= s.start.hour < 15]
    assert all(f.grid_export_kwh == 0.0 for f in noon)
    assert p.energy_from_solar_kwh > 5.0  # stored instead
    assert p.curtailed_kwh > 0  # what does not fit is curtailed, not paid for


def test_the_export_cap_limits_what_goes_to_the_grid():
    capped = BatteryParams(10, 5, 1.0, 0.9, 0.1)  # 1 kW to the grid
    p = run(battery=capped, slots=sunny_day(peak=3.0))  # a lot of sun
    assert all(f.grid_export_kwh <= 1.0 * 0.25 + 1e-9 for f in p.flows)
    assert p.curtailed_kwh > 0


def test_a_negative_buy_price_pays_to_charge_at_midday_and_not_before():
    slots = []
    for i in range(96):
        hour = i // 4
        price = -0.5 if 11 <= hour < 14 else 2.0 if 19 <= hour < 22 else 0.3  # only the evening is dear
        slots.append(Slot(T0 + timedelta(minutes=15 * i), price, price, load_kwh=0.2))
    p = run(slots=slots, terminal=0.9, battery=BatteryParams(6, 5, 5, 0.9, 0.1))
    charges = [s.start.hour for s in p.slots if s.action == "charge"]
    assert not any(h < 11 for h in charges)  # not in the cheap morning
    assert sum(1 for h in charges if 11 <= h < 14) >= 4  # but while it pays to


def test_solar_makes_early_grid_charging_unnecessary():
    no_sun = [Slot(s.start, s.buy_price, s.sell_price, load_kwh=s.load_kwh) for s in sunny_day()]
    without = run(slots=no_sun, terminal=0.0)
    with_sun = run(terminal=0.0)
    assert with_sun.energy_bought_kwh < without.energy_bought_kwh


def test_max_profit_does_not_look_at_the_panels():
    sunny = run(MAX_PROFIT, sunny_day())
    dark = run(MAX_PROFIT, [Slot(s.start, s.buy_price, s.sell_price, load_kwh=s.load_kwh) for s in sunny_day()])
    assert (sunny.net_gain, sunny.energy_from_solar_kwh) == (dark.net_gain, 0.0)


def test_self_consumption_stores_the_sun_and_exports_no_battery_energy():
    p = run(SELF_CONSUMPTION, terminal=0.9)
    assert p.energy_from_solar_kwh > 3.0
    assert p.energy_sold_kwh == pytest.approx(0.0, abs=1e-9)  # nothing from the battery goes to the grid
    for s in p.slots:
        if s.action == "discharge":
            assert s.grid_kwh == pytest.approx(s.to_house_kwh)


def test_the_accounts_add_up_with_solar():
    for mode in (BALANCED, SELF_CONSUMPTION):
        p = run(mode)
        assert p.net_gain == pytest.approx(p.earnings - p.expenses - p.degradation_cost)
        assert p.net_curve[-1] == pytest.approx(p.net_gain)


def test_energy_balance_in_every_interval():
    slots = sunny_day()
    p = run(initial=3.0)
    eff = BATTERY.round_trip_efficiency ** 0.5
    by = {s.start: s for s in p.slots}
    for i, (slot, f) in enumerate(zip(slots, p.flows)):
        step = by.get(slot.start)
        into = (step.grid_kwh + step.from_solar_kwh) if step and step.action == "charge" else 0.0
        out = step.grid_kwh if step and step.action == "discharge" else 0.0
        # What the house and panels exchange with the grid and the battery adds up.
        supply = slot.solar_kwh + f.grid_import_kwh + out
        use = slot.load_kwh + f.grid_export_kwh + f.curtailed_kwh + into
        assert supply == pytest.approx(use, abs=1e-6), i
        assert p.soc[i + 1] == pytest.approx(p.soc[i] + into * eff - out / eff, abs=0.06)


def test_without_a_battery_plan_the_flows_show_the_house_alone():
    p = run(battery=BatteryParams(10, 5, 5, 0.9, 0.1), slots=[Slot(T0 + timedelta(minutes=15 * i), 1.0, 1.0, load_kwh=0.2, solar_kwh=0.0) for i in range(8)])
    assert not p.profitable and len(p.flows) == 8
    assert all(f.grid_import_kwh == pytest.approx(0.2) for f in p.flows)
