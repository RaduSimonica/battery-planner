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
Energy already in the battery counts as free, as its cost is sunk. When two
schedules earn the same, doing nothing wins, so the planner never churns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt

MAX_SOC_UNITS = 300


@dataclass(frozen=True, slots=True)
class BatteryParams:
    capacity_kwh: float = 10.0  # usable
    max_charge_kw: float = 5.0  # drawn from the grid
    max_discharge_kw: float = 5.0  # delivered to the grid
    round_trip_efficiency: float = 0.90  # 0 < x <= 1, grid -> battery -> grid
    degradation_per_kwh: float = 0.15  # cost per kWh delivered to the grid


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime
    buy_price: float
    sell_price: float
    fraction: float = 1.0  # share of the interval still ahead (power limits scale with it)


@dataclass(frozen=True, slots=True)
class PlanSlot:
    start: datetime
    action: str  # "charge" | "discharge"
    grid_kwh: float  # drawn from (charge) or delivered to (discharge) the grid
    price: float  # buy price when charging, sell price when discharging


@dataclass(slots=True)
class Plan:
    profitable: bool = False
    net_gain: float = 0.0
    energy_bought_kwh: float = 0.0
    energy_sold_kwh: float = 0.0
    gain_per_kwh_sold: float | None = None
    gain_per_kwh_bought: float | None = None
    break_even_sell_price: float | None = None  # sell price needed above the best buy
    cycles: float = 0.0
    slots: list[PlanSlot] = field(default_factory=list)
    # Stored energy (kWh) before each slot, and after the last: len(slots_in) + 1
    # values, the first being the starting level. Flat when nothing is planned.
    soc: list[float] = field(default_factory=list)


def break_even_sell_price(buy_price: float, params: BatteryParams) -> float:
    """Sell price at which charging at ``buy_price`` just breaks even."""
    return buy_price / params.round_trip_efficiency + params.degradation_per_kwh


def plan(
    slots: list[Slot], step_hours: float, params: BatteryParams, initial_kwh: float = 0.0
) -> Plan:
    """Best schedule over ``slots`` (equal length ``step_hours`` each)."""
    result = Plan()
    if not slots or (len(slots) < 2 and initial_kwh <= 0) or params.capacity_kwh <= 0 or params.max_charge_kw <= 0 or params.max_discharge_kw <= 0:
        return result
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
    capacity = min(capacity, MAX_SOC_UNITS * step)
    units = int(capacity / step + 1e-9)
    if units < 1:
        return result
    max_charge = max(1, int(charge_cap / step + 1e-9))
    max_discharge = max(1, int(discharge_cap / step + 1e-9))

    neg = float("-inf")
    # value[t][s]: best profit from slot t on, if we hold s units entering t.
    # choice[t][s]: signed units moved (+charge/-discharge).
    value = [[neg] * (units + 1) for _ in range(n + 1)]
    choice = [[0] * (units + 1) for _ in range(n)]
    value[n] = [0.0] * (units + 1)
    for t in range(n - 1, -1, -1):
        slot = slots[t]
        frac = min(max(slot.fraction, 0.0), 1.0)
        slot_charge = int(max_charge * frac + 1e-9)
        slot_discharge = int(max_discharge * frac + 1e-9)
        for s in range(units + 1):
            # Idle first, so a tie never causes pointless cycling.
            best, best_move = value[t + 1][s], 0
            for move in range(-min(s, slot_discharge), min(units - s, slot_charge) + 1):
                nxt = value[t + 1][s + move]
                if move == 0 or nxt == neg:
                    continue
                if move > 0:  # charge: grid draw = stored / one-way efficiency
                    cash = -slot.buy_price * move * step / eff_one_way
                elif move < 0:  # discharge: deliver stored * efficiency
                    delivered = -move * step * eff_one_way
                    cash = delivered * (slot.sell_price - params.degradation_per_kwh)
                else:
                    cash = 0.0
                if cash + nxt > best + 1e-9:
                    best, best_move = cash + nxt, move
            value[t][s] = best
            choice[t][s] = best_move

    soc = min(units, round(initial_kwh / step))
    level = initial_kwh
    curve = [level]
    bought = sold = 0.0
    for t, slot in enumerate(slots):
        move = choice[t][soc]
        level = min(max(level + move * step, 0.0), params.capacity_kwh)
        curve.append(level)
        if move > 0:
            grid = move * step / eff_one_way
            bought += grid
            result.slots.append(PlanSlot(slot.start, "charge", grid, slot.buy_price))
        elif move < 0:
            grid = -move * step * eff_one_way
            sold += grid
            result.slots.append(PlanSlot(slot.start, "discharge", grid, slot.sell_price))
        soc += move

    result.net_gain = value[0][min(units, round(initial_kwh / step))]
    result.profitable = result.net_gain > 1e-9 and bool(result.slots)
    if not result.profitable:
        result.slots, result.net_gain = [], 0.0
        result.soc = [initial_kwh] * (n + 1)
        return result
    result.soc = curve
    result.energy_bought_kwh = bought
    result.energy_sold_kwh = sold
    result.gain_per_kwh_sold = result.net_gain / sold
    result.cycles = sold / params.capacity_kwh
    charge_prices = [p.price for p in result.slots if p.action == "charge"]
    if bought:
        result.gain_per_kwh_bought = result.net_gain / bought
        result.break_even_sell_price = break_even_sell_price(min(charge_prices), params)
    return result
