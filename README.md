# Battery Planner for Home Assistant

Is it worth buying electricity when it is cheap and selling it when it is dear? Battery Planner answers that for **your** battery and **your** prices, and tells you when to charge and discharge.

- **Works with any price source:** any sensor that publishes a list of buy and sell prices per interval, such as [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu) for the Romanian day-ahead market.
- **Honest economics:** round-trip efficiency, battery degradation per kWh, capacity, and **separate charge and discharge power** (they are often different, e.g. 0.3 kW in and 1.2 kW out).
- **Your real battery:** optionally pick your state-of-charge and capacity sensors, so today's plan starts from what is actually stored.
- **A schedule you can use:** net gain, gain per kWh, charge/discharge windows, the action right now, and binary sensors for automations.

## Install

HACS → Custom repositories → add this repository as *Integration* → install → restart. Then *Settings → Devices & services → Add integration → Battery Planner*.

You also need a price source. For Romania, install [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu) (0.3.0 or later) and pick its *Upcoming prices* sensor when asked.

## Setup

1. **Price source:** a sensor with a `prices` attribute (format below).
2. **Battery:** usable capacity, maximum charge and discharge power, round-trip efficiency, degradation cost per kWh, and optionally a state-of-charge sensor (%, Wh or kWh) and a capacity sensor (Wh or kWh).

Everything can be changed later under *Configure*.

### Price series format

The source sensor has an attribute `prices`:

```yaml
unit: RON/kWh
prices:
  - start: "2026-11-09T12:00:00+02:00"
    end:   "2026-11-09T12:15:00+02:00"
    buy:   1.2287    # what you pay for a kWh, tariff, taxes and VAT included
    sell:  0.9100    # what you receive for a kWh
```

Prices are per kWh, may be zero or negative, and intervals can be 15, 30 or 60 minutes (all the same length). The full description is in the [contract](https://github.com/RaduSimonica/ha-opcom-pzu/blob/main/docs/price-series.md). A template sensor can build this for any other source.

## How it plans

For one kWh bought at `b` and sold later at `s`:

```
net gain per kWh sold = s − degradation − b / efficiency
```

It is only worth cycling when that is positive. The planner finds the best schedule for the rest of the day (several cycles allowed, capacity and power respected) using the buy and sell prices of the source. Energy already stored counts as free, since its cost is sunk. **Today's** plan starts from the real state of charge (or an empty battery without a sensor) and is re-planned every 15 minutes. **Tomorrow's** plan always assumes an empty battery, as nobody knows the level by then.

| Entity | Meaning |
|---|---|
| Net gain today / tomorrow | Planned profit; attributes hold the schedule (charge/discharge windows), energy bought and sold, cycles and the break-even sell price |
| Net gain per kWh today / tomorrow | Net gain per kWh sold |
| Action | `charge` / `discharge` / `idle` right now, for automations |
| Arbitrage profitable today / tomorrow | Binary sensors |
| Energy to charge today / tomorrow | kWh the plan draws from the grid; the `windows` attribute lists when, with each window's kWh and average price |
| Energy to export today / tomorrow | kWh the plan delivers to the grid, with its `windows` |
| Projected state of charge today / tomorrow | Battery level (%) at the end of the day if the plan is followed. Attributes: `end_kwh`, `peak_kwh`, `peak_at` and a `curve` (time, kWh, %) for charts. Today starts from your real level, tomorrow from empty |
| Time to cheapest price | Minutes until the cheapest *buy* price starts (`0` = now); `at`, `ends_at` and `price` in the attributes |
| Time to best sell price | Minutes until the highest *sell* price starts |

The two timers look ahead 24 hours by default (*Configure → Timers*, 1 to 48 hours). They tick every minute.

### Using only part of the battery

*Share of the battery to use* (battery page, default 100%) keeps the rest as a reserve at the bottom that is never sold: backup power or your own use. At 50% of a 10 kWh battery the planner works with 5 kWh. With a state-of-charge sensor only the energy *above* the reserve counts, so a battery at or below the reserve has nothing to sell.

Not financial advice: results depend on your inputs and on the prices the source publishes.

## Dashboard

[`examples/dashboards/battery-plan.yaml`](examples/dashboards/battery-plan.yaml) is a ready-to-paste card (needs [apexcharts-card](https://github.com/RomRider/apexcharts-card)). Entity IDs depend on the area of the device (see the note in the file).

## Moving from OPCOM PZU 0.2.x

The battery planner used to be part of OPCOM PZU. It now lives here, and OPCOM PZU 0.3.0 only handles prices. Install this integration, pick OPCOM PZU's *Upcoming prices* sensor, and enter your battery settings again; the entity names are now `sensor.battery_planner_...`.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md): each pull request into `main` bumps the version and gets a release.
