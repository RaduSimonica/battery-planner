# Battery Planner for Home Assistant

Is it worth buying electricity when it is cheap and selling it when it is dear? Battery Planner answers that for **your** battery and **your** prices, and tells you when to charge and discharge.

- **Works with any price source:** any sensor that publishes a list of buy and sell prices per interval, such as [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu) for the Romanian day-ahead market.
- **Honest economics:** round-trip efficiency, battery degradation per kWh, capacity, and **separate charge and discharge power** (they are often different, e.g. 0.3 kW in and 1.2 kW out).
- **Your real battery:** optionally pick your state-of-charge and capacity sensors, so today's plan starts from what is actually stored.
- **A schedule you can use:** net gain, gain per kWh, charge/discharge windows, the action right now, and binary sensors for automations.

## Install

HACS → Custom repositories → add this repository as *Integration* → install → restart. Then *Settings → Devices & services → Add integration → Battery Planner*.

You also need a price source. For Romania, install [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu) (0.3.0 or later) and pick its *Upcoming prices* sensor when asked.

**Snapshots:** new things first appear as pre-releases (`vX.Y.Z-snapshot.N`). To try them, open the repository in HACS, menu → *Show beta versions*, then update. Stable releases are published once in a while from the snapshots that proved fine.

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

It is only worth cycling when that is positive. The planner finds the best schedule for the rest of the day (several cycles allowed, capacity and power respected) using the buy and sell prices of the source. Energy already stored counts as free, since its cost is sunk. **Today's** plan starts from the real state of charge (or an empty battery without a sensor) and is re-planned every 15 minutes. **Tomorrow's** plan starts where today's plan leaves the battery.

| Entity | Meaning |
|---|---|
| Net gain today / tomorrow | Earnings minus expenses minus battery wear, against not using the battery. Attributes hold the schedule (charge/discharge windows), energy bought, sold and used by the house, cycles, the break-even sell price and the mode |
| Net result today / tomorrow | The net gain plus the value of the energy the plan leaves in the battery, at what it would cost to buy again. Use it to compare the modes: a mode that runs the battery down looks better in the net gain and worse here, because the refill is paid the next day |
| Plan | The whole plan interval by interval for charts and cards: the `plan` attribute lists price, action, energy bought, delivered, to the house and exported, expected house use, battery level and running earnings, expenses and net result, from now to the end of tomorrow |
| Earnings today / tomorrow | Money received for energy sold, plus the purchases avoided when the battery powers the house |
| Expenses today / tomorrow | Money spent buying energy for the battery |
| Planning mode | Select: *Max profit*, *Cheap self-consumption* or *Balanced*. Remembered across restarts |
| Net gain per kWh today / tomorrow | Net gain per kWh sold |
| Action | `charge` / `discharge` / `idle` right now, for automations |
| Arbitrage profitable today / tomorrow | Binary sensors |
| Energy to charge today / tomorrow | kWh the plan draws from the grid; the `windows` attribute lists when, with each window's kWh and average price |
| Energy to export today / tomorrow | kWh the plan delivers to the grid, with its `windows` |
| Projected state of charge today / tomorrow | Battery level (%) at the end of the day if the plan is followed. Attributes: `end_kwh`, `peak_kwh`, `peak_at` and a `curve` (time, kWh, %) for charts. Today starts from your real level, tomorrow from empty |
| House power, Expected consumption next 24 hours | Only with the consumption sensors above. The expected value comes with an hourly `forecast` |
| Time to cheapest price | Minutes until the cheapest *buy* price starts (`0` = now); `at`, `ends_at` and `price` in the attributes |
| Time to best sell price | Minutes until the highest *sell* price starts |

The two timers look ahead 24 hours by default (*Configure → Timers*, 1 to 48 hours). They tick every minute.

### Planning modes

The *Planning mode* select (also usable from an automation) changes what the plan optimises:

| Mode | What it does |
|---|---|
| **Max profit** (default) | Only buys cheap and sells dear. The house is ignored. |
| **Cheap self-consumption** | Powers the house at the lowest cost. The battery charges in the cheapest hours and discharges only up to what the house uses, never to export. A kWh it saves is worth the *buy* price it avoids. |
| **Balanced** | Covers the house first, then exports what is left when the sell price pays. |

The two house modes need the consumption forecast (see below). Until it has history the plan falls back to max profit and says so in the `mode_note` attribute. Energy left in the battery at the end of the day is worth what it would cost to buy again (the cheapest buy price in the next day and a half), so the plan does not sell the battery empty when the house will need it tomorrow. The house's expected use per interval is the hourly forecast spread over its quarter hours. Solar is not part of the plan yet.

### House consumption (optional)

*Configure → House consumption.* Pick how the house's power is measured: a sensor of the house itself, **one grid sensor** (positive = import; tick *flip* if yours reports export as positive), or **two grid sensors** (import and export). With a grid sensor you can add solar production and battery power (positive = charging), so energy that fills the battery or comes from the sun is not counted as house use. An **EV charger** sensor can be added too: it is left out of the house's consumption, because it is a large, unpredictable load that is rarely charged from the home battery.

The planner learns your consumption from Home Assistant's long-term statistics of these sensors (hourly, so they need `state_class: measurement` and a unit of W or kW; most power sensors qualify). It uses the last four weeks, separately for weekdays and weekends, and starts with the history you already have. *Expected consumption next 24 hours* shows the result with the hourly forecast in an attribute. The *Cheap self-consumption* and *Balanced* modes use it.

### Using only part of the battery

*Share of the battery to use* (battery page, default 100%) keeps the rest as a reserve at the bottom that is never sold: backup power or your own use. At 50% of a 10 kWh battery the planner works with 5 kWh. The reserve is a floor: the planner never discharges below it. With a state-of-charge sensor the plan starts from the real level, so a battery at or below the reserve has nothing to sell, and energy bought to refill it is not counted as sellable.

Not financial advice: results depend on your inputs and on the prices the source publishes.

## Dashboards

Ready-to-paste cards are in [`examples/dashboards/`](examples/dashboards) (they need [apexcharts-card](https://github.com/RomRider/apexcharts-card)). All three read the *Plan* sensor, so they show the next intervals as the plan is redone:

| File | Shows |
|---|---|
| `battery-plan.yaml` | When the battery charges from the grid and discharges, over the buy and sell prices |
| `battery-soc.yaml` | The planned battery level, the energy moved in each interval and the house's expected use |
| `battery-gain.yaml` | The running earnings, expenses and net result over the plan |

Entity IDs depend on the area of the device (see the note in each file).

### The plan follows what you do

The plan is redone every 15 minutes from the real battery level. It is also redone sooner when the house uses clearly more or less than assumed for now (an oven, a washing machine), or when the battery holds clearly more or less than planned. The live house power, averaged over about ten minutes, shapes the next hour or two and then fades back to the usual pattern.

## Moving from OPCOM PZU 0.2.x

The battery planner used to be part of OPCOM PZU. It now lives here, and OPCOM PZU 0.3.0 only handles prices. Install this integration, pick OPCOM PZU's *Upcoming prices* sensor, and enter your battery settings again; the entity names are now `sensor.battery_planner_...`.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md): each pull request into `main` bumps the version and gets a release.
