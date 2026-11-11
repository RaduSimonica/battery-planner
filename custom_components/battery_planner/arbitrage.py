"""Battery arbitrage planner.

Given buy/sell prices per interval, find the charge/discharge schedule that
maximises net profit for a battery with limited capacity and power.

Economics of one kWh delivered to the grid, charged earlier at price ``b`` and
sold at ``s``::

    net = s - degradation - b / round_trip_efficiency

so a pair is only worth cycling when ``s - degradation > b / efficiency``.
The planner is a dynamic programme over a discretised state of charge, which
handles capacity and power limits and multiple cycles per day. The battery
starts with ``initial_kwh`` (default empty) and may end at any level; energy
left over at the end is worth nothing, so nothing is bought just to sit there.
A reserve (``min_kwh``) is never discharged, though the battery may start below it
and charging it back up is allowed. Energy already in the battery counts as free,
as its cost is sunk. When two
schedules earn the same, doing nothing wins, so the planner never churns.

Modes. ``max_profit`` ignores the house: the battery only buys and sells. The
other two know how much the house uses in each slot (``Slot.load_kwh``). Energy
leaving the battery first covers the house, worth the *buy* price it avoids, and
only the rest is exported at the *sell* price. ``balanced`` may export;
``self_consumption`` never does (the battery only discharges up to the load).
Energy still in the battery at the end is worth ``terminal_value`` per kWh (what
it would cost to buy again), so the plan does not empty it just because the
horizon ends.

Solar (``Slot.solar_kwh``, house modes only). Production first covers the house;
the surplus can charge the battery for free, or be exported at the sell price. Storing
it is therefore worth what it can earn later, minus the export it gives up now. What
cannot be stored or sold is curtailed: the surplus beyond the export cap (the battery's
discharge limit stands for it), and any surplus while the sell price is negative.
Importing at a negative buy price pays, so the plan charges from the grid then.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt

MAX_SOC_UNITS = 300

MAX_PROFIT = "max_profit"
BALANCED = "balanced"
SELF_CONSUMPTION = "self_consumption"
MODES = (MAX_PROFIT, SELF_CONSUMPTION, BALANCED)


@dataclass(frozen=True, slots=True)
class BatteryParams:
    capacity_kwh: float = 10.0  # the whole battery
    max_charge_kw: float = 5.0  # drawn from the grid
    max_discharge_kw: float = 5.0  # delivered to the grid
    round_trip_efficiency: float = 0.90  # 0 < x <= 1, grid -> battery -> grid
    degradation_per_kwh: float = 0.15  # cost per kWh delivered to the grid
    min_kwh: float = 0.0  # reserve: never discharged below this level (charging is always allowed)


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime
    buy_price: float
    sell_price: float
    fraction: float = 1.0  # share of the interval still ahead (power limits scale with it)
    load_kwh: float = 0.0  # what the house uses in the part of the interval still ahead
    solar_kwh: float = 0.0  # what the panels make in the part of the interval still ahead


@dataclass(frozen=True, slots=True)
class PlanSlot:
    start: datetime
    action: str  # "charge" | "discharge"
    grid_kwh: float  # drawn from the grid (charge) or delivered by the battery (discharge)
    price: float  # buy price when charging; value per kWh delivered when discharging
    to_house_kwh: float = 0.0  # the part of a discharge that covers the house, not the grid
    from_solar_kwh: float = 0.0  # the part of a charge that comes from the panels, not the grid


@dataclass(frozen=True, slots=True)
class SlotFlow:
    """What the house exchanges with the grid in one interval, with the plan."""

    grid_import_kwh: float = 0.0
    grid_export_kwh: float = 0.0
    solar_to_battery_kwh: float = 0.0
    curtailed_kwh: float = 0.0  # solar that could be neither stored nor sold


@dataclass(slots=True)
class Plan:
    profitable: bool = False
    net_gain: float = 0.0  # earnings - expenses - degradation, against not using the battery
    earnings: float = 0.0  # money received for exports, plus purchases avoided at home
    expenses: float = 0.0  # money spent buying energy for the battery
    degradation_cost: float = 0.0
    stored_value: float = 0.0  # change in the value of the energy left in the battery
    mode: str = MAX_PROFIT
    energy_bought_kwh: float = 0.0
    energy_sold_kwh: float = 0.0  # exported to the grid
    energy_to_house_kwh: float = 0.0  # covers the house instead of being bought
    energy_from_solar_kwh: float = 0.0  # charged from the panels' surplus
    curtailed_kwh: float = 0.0
    flows: list[SlotFlow] = field(default_factory=list)  # per interval, also when nothing is planned
    gain_per_kwh_sold: float | None = None
    gain_per_kwh_bought: float | None = None
    break_even_sell_price: float | None = None  # sell price needed above the best buy
    cycles: float = 0.0
    slots: list[PlanSlot] = field(default_factory=list)
    # Stored energy (kWh) before each slot, and after the last: len(slots_in) + 1
    # values, the first being the starting level. Flat when nothing is planned.
    soc: list[float] = field(default_factory=list)
    # Running totals before each slot and after the last (len(slots_in) + 1 values, from 0):
    # money received, money spent, and the cash result (earnings - expenses - wear).
    earnings_curve: list[float] = field(default_factory=list)
    expenses_curve: list[float] = field(default_factory=list)
    net_curve: list[float] = field(default_factory=list)


def break_even_sell_price(buy_price: float, params: BatteryParams) -> float:
    """Sell price at which charging at ``buy_price`` just breaks even."""
    return buy_price / params.round_trip_efficiency + params.degradation_per_kwh


def plan(
    slots: list[Slot],
    step_hours: float,
    params: BatteryParams,
    initial_kwh: float = 0.0,
    mode: str = MAX_PROFIT,
    terminal_value: float = 0.0,
) -> Plan:
    """Best schedule over ``slots`` (equal length ``step_hours`` each)."""
    result = Plan(mode=mode)
    if not slots or (len(slots) < 2 and initial_kwh <= 0) or params.capacity_kwh <= 0 or params.max_charge_kw <= 0 or params.max_discharge_kw <= 0:
        return result
    house = mode in (BALANCED, SELF_CONSUMPTION)  # the house and the panels are part of the plan
    only_house = mode == SELF_CONSUMPTION  # the battery never exports
    terminal = terminal_value if house else 0.0
    eff = min(max(params.round_trip_efficiency, 0.01), 1.0)
    eff_one_way = sqrt(eff)

    # Discretise stored energy. What a slot can move, in stored kWh, differs
    # for charging and discharging (the batteries and inverters often differ),
    # and sets the grain. Capacity beyond what the horizon could ever fill is
    # ignored, which keeps the state space small for large batteries.
    n = len(slots)
    charge_cap = params.max_charge_kw * step_hours * eff_one_way
    discharge_cap = params.max_discharge_kw * step_hours / eff_one_way
    initial_kwh = min(max(initial_kwh, 0.0), params.capacity_kwh)
    capacity = min(params.capacity_kwh, n * charge_cap + initial_kwh)
    step = min(charge_cap, discharge_cap, capacity / 10) / 2
    if house:
        # The grain must be finer than what the house uses in a slot (a quarter of it, so a
        # discharge hardly overshoots the load and is not sold as noise), or a battery
        # that only covers the house could never discharge in a single step.
        mean_load = sum(s.load_kwh for s in slots) / n
        step = min(step, max(mean_load / 4, capacity / MAX_SOC_UNITS))
    capacity = min(capacity, MAX_SOC_UNITS * step)
    units = int(capacity / step + 1e-9)
    if units < 1:
        return result
    floor = min(units, max(0, round(min(params.min_kwh, params.capacity_kwh) / step)))
    max_charge = max(1, int(charge_cap / step + 1e-9))
    max_discharge = max(1, int(discharge_cap / step + 1e-9))

    def flows(slot: Slot, move: int) -> tuple[float, float, float, float, float, float, float, float]:
        """One interval with ``move`` units into (+) or out of (-) the battery.

        Returns grid import, grid export, curtailed, delivered by the battery,
        to the house, charged from the grid, charged from solar, and the cash.
        """
        frac = min(max(slot.fraction, 0.0), 1.0)
        load, solar = (slot.load_kwh, slot.solar_kwh) if house else (0.0, 0.0)
        surplus, deficit = max(solar - load, 0.0), max(load - solar, 0.0)
        out = to_house = from_grid = from_solar = 0.0
        if move > 0:  # charge: the surplus first, the grid for the rest
            into = move * step / eff_one_way
            from_solar = min(surplus, into)
            from_grid = into - from_solar
            imported, spare_solar, from_battery = deficit + from_grid, surplus - from_solar, 0.0
        elif move < 0:  # discharge: the house first, then the grid
            out = -move * step * eff_one_way
            to_house = min(out, deficit)
            imported, spare_solar, from_battery = deficit - to_house, surplus, out - to_house
        else:
            imported, spare_solar, from_battery = deficit, surplus, 0.0
        # Spare solar goes to the grid up to the export cap, and not at all while it would cost
        # money: that part is curtailed. Energy from the battery goes where it is told.
        cap = params.max_discharge_kw * step_hours * frac
        sold_solar = min(spare_solar, max(cap - from_battery, 0.0)) if slot.sell_price >= 0 else 0.0
        exported = from_battery + sold_solar
        cash = -imported * slot.buy_price + exported * slot.sell_price - out * params.degradation_per_kwh
        return imported, exported, spare_solar - sold_solar, out, to_house, from_grid, from_solar, cash

    # Cash of every possible move in every slot, against doing nothing (the battery
    # is not there). It does not depend on the level, so it is worked out once.
    cash_by_move: list[dict[int, tuple[float, float, float, float]]] = []
    idle_flows: list[tuple[float, ...]] = []
    for slot in slots:
        frac = min(max(slot.fraction, 0.0), 1.0)
        slot_charge = int(max_charge * frac + 1e-9)
        slot_discharge = int(max_discharge * frac + 1e-9)
        idle = flows(slot, 0)
        idle_flows.append(idle)
        table: dict[int, tuple[float, float, float, float]] = {}
        load = slot.load_kwh if house else 0.0
        deficit = max(load - (slot.solar_kwh if house else 0.0), 0.0)
        for move in range(-slot_discharge, slot_charge + 1):
            if move == 0:
                continue
            f = flows(slot, move)
            if only_house and move < 0 and f[3] > deficit + 1e-9:
                continue  # more than the house needs would be exported
            table[move] = (f[7] - idle[7], f[0], f[1], f[2])
        cash_by_move.append({m: v for m, v in table.items()})

    neg = float("-inf")
    # value[t][s]: best profit from slot t on, if we hold s units entering t.
    # choice[t][s]: signed units moved (+charge/-discharge).
    value = [[neg] * (units + 1) for _ in range(n + 1)]
    choice = [[0] * (units + 1) for _ in range(n)]
    value[n] = [max(s - floor, 0) * step * terminal for s in range(units + 1)]
    for t in range(n - 1, -1, -1):
        table = cash_by_move[t]
        for s in range(units + 1):
            # Idle first, so a tie never causes pointless cycling.
            best, best_move = value[t + 1][s], 0
            # Discharging stops at the reserve; a battery already below it only charges.
            low = -max(s - floor, 0)
            high = units - s
            for move, (cash, *_rest) in table.items():
                if move < low or move > high:
                    continue
                nxt = value[t + 1][s + move]
                if nxt == neg:
                    continue
                if cash + nxt > best + 1e-9:
                    best, best_move = cash + nxt, move
            value[t][s] = best
            choice[t][s] = best_move

    start_units = min(units, round(initial_kwh / step))
    soc = start_units
    level = initial_kwh
    curve = [level]
    earn_curve, spend_curve, net_curve = [0.0], [0.0], [0.0]
    bought = delivered_total = to_house = solar_stored = curtailed = 0.0
    earnings = expenses = degradation = 0.0
    plan_flows: list[SlotFlow] = []
    for t, slot in enumerate(slots):
        move = choice[t][soc]
        level = min(max(level + move * step, 0.0), params.capacity_kwh)
        curve.append(level)
        imported, exported, cut, out, covers, from_grid, from_solar, _cash = flows(slot, move)
        i0, e0 = idle_flows[t][0], idle_flows[t][1]
        plan_flows.append(SlotFlow(imported, exported, from_solar, cut))
        # Against no battery: purchases avoided and extra exports are earnings; extra
        # purchases and exports given up are expenses.
        for contribution in (-(imported - i0) * slot.buy_price, (exported - e0) * slot.sell_price):
            if contribution >= 0:
                earnings += contribution
            else:
                expenses -= contribution
        degradation += out * params.degradation_per_kwh
        if move > 0:
            bought += from_grid
            solar_stored += from_solar
            result.slots.append(
                PlanSlot(slot.start, "charge", from_grid, slot.buy_price, from_solar_kwh=from_solar)
            )
        elif move < 0:
            delivered_total += out
            to_house += covers
            value_out = covers * slot.buy_price + (out - covers) * slot.sell_price
            result.slots.append(PlanSlot(slot.start, "discharge", out, value_out / out, covers))
        curtailed += cut
        soc += move
        earn_curve.append(earnings)
        spend_curve.append(expenses)
        net_curve.append(earnings - expenses - degradation)

    # Stored energy above the reserve at the end, against at the start, at its replacement cost.
    stored = (max(soc - floor, 0) - max(start_units - floor, 0)) * step * terminal
    cash = earnings - expenses - degradation
    result.net_gain = cash
    result.profitable = bool(result.slots) and cash + stored > 1e-9
    if not result.profitable:
        result.slots, result.net_gain = [], 0.0
        result.soc = [initial_kwh] * (n + 1)
        result.flows = [
            SlotFlow(f[0], f[1], 0.0, f[2]) for f in idle_flows
        ]  # the house as it would be without a battery
        return result
    result.soc = curve
    result.flows = plan_flows
    result.earnings_curve, result.expenses_curve, result.net_curve = earn_curve, spend_curve, net_curve
    result.earnings, result.expenses, result.degradation_cost = earnings, expenses, degradation
    result.stored_value = stored
    result.energy_bought_kwh = bought
    result.energy_to_house_kwh = to_house
    result.energy_from_solar_kwh = solar_stored
    result.energy_sold_kwh = delivered_total - to_house
    result.curtailed_kwh = curtailed
    if delivered_total:
        result.gain_per_kwh_sold = result.net_gain / delivered_total
    result.cycles = delivered_total / params.capacity_kwh
    charge_prices = [p.price for p in result.slots if p.action == "charge" and p.grid_kwh > 0]
    if bought and charge_prices:
        result.gain_per_kwh_bought = result.net_gain / bought
        result.break_even_sell_price = break_even_sell_price(min(charge_prices), params)
    return result
