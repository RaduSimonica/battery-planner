# Battery Planner for Home Assistant

Is it worth buying electricity when it is cheap and selling it when it is dear? Battery Planner answers that for **your** battery and **your** prices, and tells you when to charge and discharge.

- **Works with any price source:** any sensor that publishes a list of buy and sell prices per interval, such as [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu) for the Romanian day-ahead market, or **fixed prices** (with an optional second rate, such as a night rate) for a fixed tariff.
- **Honest economics:** round-trip efficiency, battery degradation per kWh, capacity, and **separate charge and discharge power** (they are often different, e.g. 0.3 kW in and 1.2 kW out).
- **Your real battery:** optionally pick your state-of-charge and capacity sensors, so today's plan starts from what is actually stored.
- **A schedule you can use:** net gain, gain per kWh, charge/discharge windows, the action right now, and binary sensors for automations.

> **Beta.** Battery Planner works, but it is young and has not seen a summer yet. Please report what you find in the [issues](https://github.com/RaduSimonica/battery-planner/issues) and attach the diagnostics file (*Settings → Devices & services → Battery Planner → three dots → Download diagnostics*).

## Known limitations

- **The plan advises; it does not control your inverter.** The *Action* sensor and the planned battery level are for you and your automations. An inverter in its normal mode may do something else, for example charge from surplus solar until the battery is full, even where the plan would stop earlier.
- **Solar forecasts** are read from the Energy dashboard. Forecast.Solar was checked against real data; other providers are assumed to mark each value with the start of its period, as Home Assistant does, and have not been checked.
- **Surplus is assumed to be curtailable** (for example by limiting the solar chargers) when it cannot be stored or sold at a positive price.
- **The consumption forecast is an average** per hour of the day for weekdays and weekends. It does not know about the weather, or that a heat pump works harder on a cold day. The live correction helps for an hour or two.
- **The prices decide everything.** Make sure the price source gives what you really pay and receive, including regulated tariffs, VAT and the way your supplier credits energy you send to the grid.
- In *Max profit* mode the net result equals the net gain: that mode places no value on the energy left in the battery. Compare the modes with the *Balanced* and *Cheap self-consumption* figures.

## Install

HACS → Custom repositories → add this repository as *Integration* → install → restart. Then *Settings → Devices & services → Add integration → Battery Planner*.

You also need a price source. For a spot market tariff in Romania, install [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu) (0.3.0 or later) and pick its *Upcoming prices* sensor when asked. With a fixed tariff you do not need another integration: choose *Fixed prices* instead.

**Snapshots:** new things first appear as pre-releases (`vX.Y.Z-snapshot.N`). To try them, open the repository in HACS, menu → *Show beta versions*, then update. Stable releases are published once in a while from the snapshots that proved fine.

## Setup

1. **Price source:** a sensor with a `prices` attribute (format below), or fixed prices (below).
2. **Battery:** usable capacity, maximum charge and discharge power, round-trip efficiency, degradation cost per kWh, and optionally a state-of-charge sensor (%, Wh or kWh) and a capacity sensor (Wh or kWh).

Everything can be changed later under *Configure*.

### Fixed prices

For a fixed tariff, choose *Fixed prices* as the price source (*Configure → Price source* to switch later). Enter the **buy price** (what you pay for a kWh) and the **sell price** (what you receive for a kWh sent to the grid, 0 if you are not paid for it), per kWh in your Home Assistant currency, with taxes, network charges and VAT included, as on your bill.

If your tariff has another rate at some hours, for example a night rate, add a **second rate**: the time it starts and ends (local time, it may run past midnight), its buy and sell price, and the days it applies to (every day, weekdays or weekends; the day an interval falls on decides it). More periods are on the [roadmap](ROADMAP.md).

With flat prices there is nothing to buy cheap and sell dear, but the planner still helps wherever the sun and the house are concerned. In the *Balanced* and *Cheap self-consumption* modes it stores surplus solar for later when the buy price is higher than the sell price, and with a night rate it fills the battery at the cheap rate for the hours that cost more. The *Free energy* signal turns on when surplus sun would go to the grid for nothing.

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
| Expected solar today / tomorrow | The solar forecast from the Energy dashboard (only when one exists), added up over all providers |
| Earnings today / tomorrow | Money received for energy sold, plus the purchases avoided when the battery powers the house |
| Expenses today / tomorrow | Money spent buying energy for the battery |
| Planning mode | Select: *Max profit*, *Cheap self-consumption* or *Balanced*. Remembered across restarts |
| Net gain per kWh today / tomorrow | Net gain per kWh sold |
| Action | `charge` / `discharge` / `idle` right now, for automations |
| Arbitrage profitable today / tomorrow | Binary sensors |
| Free energy | On while energy costs nothing or would be wasted: a buy price of zero or less, or solar surplus left after the battery that would be exported for nothing (zero or negative sell price) or cannot be stored or sold. A signal for your automations to run heavy loads. Attributes: `reason`, `until`, `next_start`, `next_end` and `windows` |
| Energy to charge today / tomorrow | kWh the plan draws from the grid; the `windows` attribute lists when, with each window's kWh and average price |
| Energy to export today / tomorrow | kWh the plan delivers to the grid, with its `windows` |
| Projected state of charge today / tomorrow | Battery level (%) at the end of the day if the plan is followed. Attributes: `end_kwh`, `peak_kwh`, `peak_at` and a `curve` (time, kWh, %) for charts. Today starts from your real level, tomorrow from empty |
| House power, Expected consumption next 24 hours | Only with the consumption sensors above. The expected value comes with an hourly `forecast` |
| Expected consumption with EV next 24 hours | Only with an EV charger sensor: the same forecast with the car left in. `ev_kwh` is the car's share and `without_ev_kwh` the house alone. The plan keeps using the house-only forecast |
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

The two house modes need the consumption forecast (see below). Until it has history the plan falls back to max profit and says so in the `mode_note` attribute. Energy left in the battery at the end of the day is worth what it would cost to buy again (the cheapest buy price in the next day and a half), so the plan does not sell the battery empty when the house will need it tomorrow. The house's expected use per interval is the hourly forecast spread over its quarter hours. Solar is part of both house modes (see below); max profit ignores the house and the panels.

### Solar

If your Energy dashboard has solar forecasts (Forecast.Solar, Solcast, Open-Meteo and others), the planner uses them automatically, all of them added up, so several arrays with a forecast each work. Nothing to set up. In the *Balanced* and *Cheap self-consumption* modes the plan then:

- covers the house with the sun first, and lets the surplus **charge the battery for free** instead of exporting it when the price is low;
- sells that energy **later, when the price is high**, and exports the surplus anyway when the battery has no room;
- never exports at a **negative sell price** (the surplus is stored, or curtailed if there is no room; some plans, like E.ON's pass-through, can go negative, while PPC floors the sell price at 0 and Yellowgrid at 0.50). A negative *buy* price is handled too (it would be charged from the grid), but supplier plans rarely produce one, because network charges and taxes are paid on every imported kWh;
- curtails surplus beyond your **grid export limit**. Enter that limit in the battery's *Maximum discharge power*: the lower of the battery's own limit and the export limit you are allowed.

Forecast.Solar marks each value with the *end* of its period (plus extra stamps at sunrise and sunset) while most other providers use the start; the planner reads each the right way, checked against real data.

Add your solar production sensors under *House consumption* (several are added up) and the live production shapes the next hour: a cloud that is not in the forecast shows up in the plan within minutes. *Expected solar today / tomorrow* show the forecast. The *Plan* rows carry `solar_kwh`, `solar_to_battery_kwh`, `grid_import_kwh`, `grid_export_kwh` and `curtailed_kwh` for charts.

### Free energy

The planner only plans the battery, but the *Free energy* binary sensor tells your automations when it is a good moment for heavy loads (heat pump, washing machine, EV). It is on while the next kWh costs nothing or would be wasted: a buy price of zero or less, or solar surplus that the battery cannot absorb and that would go to the grid for nothing (a sell price of zero or less) or cannot be sold at all. The battery is assumed to charge greedily from surplus until it is full, as an inverter in its normal mode does, even where the plan would stop earlier, so only what it cannot absorb counts. `windows` lists every such stretch ahead, and `next_start` / `next_end` give the next one when it is off.

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.battery_planner_free_energy
    to: "on"
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.washing_machine_socket
```

### House consumption (optional)

*Configure → House consumption.* Pick how the house's power is measured: a sensor of the house itself, **one grid sensor** (positive = import; tick *flip* if yours reports export as positive), or **two grid sensors** (import and export). With a grid sensor you can add solar production and battery power (positive = charging), so energy that fills the battery or comes from the sun is not counted as house use. An **EV charger** sensor can be added too: it is left out of the house's consumption, because it is a large, unpredictable load that is rarely charged from the home battery.

The planner learns your consumption from Home Assistant's long-term statistics of these sensors (hourly, so they need `state_class: measurement` and a unit of W or kW; most power sensors qualify). It uses the last four weeks, separately for weekdays and weekends, and starts with the history you already have. *Expected consumption next 24 hours* shows the result with the hourly forecast in an attribute. With an EV charger sensor, *Expected consumption with EV next 24 hours* adds the car back, for the day's total. It is an average of what the car drew at each hour over four weeks, so it spreads out charging sessions that really happen on a few nights: good for the total, not for predicting which night you plug in. If your charger follows the solar surplus, much of that energy is sun, not grid energy. The *Cheap self-consumption* and *Balanced* modes use it.

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
