# Roadmap

Planned improvements, not implemented yet. Each ships as a version of its own
(see [CONTRIBUTING.md](CONTRIBUTING.md)). Prices come from other integrations
through the [price series contract](https://github.com/RaduSimonica/ha-opcom-pzu/blob/main/docs/price-series.md);
the OPCOM PZU roadmap is in its own repository.

## 1. [Battery Planner] Battery share: how much of the battery to use

Users choose a percentage of the battery the planner may work with, e.g. 50%.

- New battery option `battery_usable_pct` (1 to 100, default 100), in the
  battery page of the options flow.
- Meaning: the planner only uses that share of the capacity. The rest is a
  **reserve at the bottom of the battery that is never touched** (backup power,
  self-consumption). At 50% of a 10 kWh battery the planner works with 5 kWh.
- With a state-of-charge sensor, only the energy *above* the reserve counts as
  available: `available = clamp(stored - reserve, 0, usable)`. A battery at or
  below the reserve has nothing to sell. The SoC sensor still reports the whole
  battery.
- Plan attributes: keep `capacity_kwh` as the share the planner may use and add
  `reserve_kwh`.
- Tests: usable share caps the plan's capacity; SoC below the reserve gives
  zero available energy; with 100% nothing changes. Add the field to the
  English and Romanian translations and the README.

## 4. [Battery Planner] Countdown sensors: time to the cheapest price

Timer sensors that count down in minutes.

- **Time to cheapest price:** minutes until the cheapest *buy* price among the
  upcoming intervals starts. **Time to best sell price:** the same for the
  highest *sell* price. Both use the user's own buy and sell prices.
- Window: from the current interval to a look-ahead horizon, default 24 hours
  (option `timer_horizon_hours`, 1 to 48, in the general options page). If
  tomorrow is not published yet, the window ends with today's data. Ties pick
  the earliest interval. `0` when the best interval is the current one.
- State in minutes (`device_class: duration`, unit `min`), rounded up so it
  reads 1 until the interval starts. Attributes: `at` (start, local time),
  `ends_at`, `price`.
- The state must tick every minute, not only on the 15-minute tick: the
  entities register their own minute timer (`async_track_utc_time_change` with
  `second=1`) instead of making every sensor update each minute.
- Tests: expected minutes computed independently from the fixture CSV, and the
  value dropping by one after a minute passes. Add translations and a README
  entry; a dashboard example is optional.
## 6. [Battery Planner] Energy and state-of-charge forecast sensors

What the plan will do, in energy rather than money. Built from the same plan
as the battery gain sensors (today from the real state of charge, tomorrow from
empty), so they stay consistent with it.

- **Energy to charge** and **energy to export** per plan: kWh drawn from the
  grid in the cheap windows and delivered to the grid in the expensive ones
  (today: the part still ahead; tomorrow: the whole day). Attributes: the
  windows they come from, with each window's kWh and average price.
- **Projected state of charge**: the battery level at the end of the day if the
  plan is followed, as kWh and as a percentage of capacity (the percentage
  needs the capacity, so it follows the capacity sensor when one is set).
- **State of charge curve** as an attribute (a point per interval, from now to
  the end of the plan) so a chart or card can draw it. Also expose the peak
  level and when it is reached.
- The planner already walks the battery level interval by interval, so it only
  has to return that curve (`Plan.soc`) next to the slots. Tests: the curve
  starts at the real level, never leaves 0..capacity, ends at start + bought
  (net of efficiency) - sold, and the energy sensors equal the sums over the
  schedule windows. Translations and README.

## 7. [Battery Planner, price card in OPCOM PZU] Custom dashboard card(s)

apexcharts-card works but is not the nicest to look at, and each example needs
a long block of YAML. A card made for this integration would look better and be
one line to add.

- **Battery card**: a battery shape showing the current level (from the SoC
  sensor) and the projected level at the end of the day, with what the plan does
  now (charging, exporting, idle), the energy to charge and export (item 6) and
  the next change. Colours follow the theme.
- **Price card**: the merged price chart of item 3 (spot, buy, sell, now
  marker, cheap and expensive windows shaded, the planned charge and discharge
  windows marked), instead of an apexcharts configuration.
- Delivery: a single JavaScript file in the repository, registered as a
  dashboard resource by the integration so HACS users do not add it by hand
  (or documented as one step if that is not possible). Cards take the entity
  from their configuration and read the existing attributes, so no new backend
  work is needed beyond items 3 and 6. Needs a visual editor for the card
  options, a preview in the card picker, and light and dark theme checks.
- Decide first whether it is one card with options or two, and whether to build
  on Lit (as Home Assistant does) or plain web components. Keep the apexcharts
  examples as the fallback.

## 8. [Battery Planner] Solar and house consumption in the plan

Take the user's solar production, consumption and solar forecast into account,
so the planner knows how much of the battery can really be sold, and does not
sell energy the house will need later and buy back at a higher price.

**Why the current planner is not enough.** It values every kWh at the market:
buy to charge, sell to discharge. With solar and a house load, energy that
leaves the battery to power the house is worth the *buy* price it avoids, not
the sell price, and surplus solar is free energy to store. The battery's use for
arbitrage is whatever is left after the house's needs, and that depends on the
forecast.

**Model.** For each interval, the house needs `net = consumption - solar`
(negative = surplus). The grid exchange is `net` plus charging minus
discharging (battery power limits unchanged). Importing costs the buy price,
exporting earns the sell price. The planner minimises the total cost over the
horizon with the same dynamic programme (the per-interval cost is just
piecewise linear), and the *gain* is the cost without the battery minus the
cost with it. Nothing else changes: degradation per kWh discharged,
efficiency, separate charge and discharge power, start level from the SoC
sensor.

- Arbitrage then happens only when it beats using the energy at home, and the
  "reserve for the house" is an outcome, not a setting. Item 1 (usable share)
  stays as a manual floor on top of it.
- Tomorrow's plan can start from today's *projected* end-of-day level instead of
  an empty battery (item 6 provides it), which is a better assumption once
  solar and load are known.

**Inputs (all optional, all existing sensors the user picks):**
- *Solar production now* (W or kW) and *house consumption now* (W or kW), used
  for the current interval and to correct the forecast.
- *Solar forecast*: the Home Assistant energy platform already offers one
  (Forecast.Solar, Solcast and others register as forecast providers, hourly
  Wh), so use that through the Energy dashboard's configured solar source
  rather than asking for a sensor; fall back to a sensor with a forecast
  attribute.
- *Consumption forecast*: there is no ready one, so learn it from the
  consumption sensor's own long-term statistics: the average per time of day
  over the last weeks, separately for weekdays and weekends. Until enough
  history exists, use a flat value from the last 24 hours.
- Without any of these the planner behaves exactly as today.

**New sensors:** expected self-consumption (share of the house load covered by
solar and battery), grid import avoided, energy the battery can export for
arbitrage ("usable for arbitrage", kWh), energy kept as house reserve, and the
projected state of charge curve of item 6 including solar and load.

**Care needed:**
- Forecasts are wrong. Show the assumptions in attributes, re-plan every 15
  minutes with the real readings (already done for the SoC), and consider a
  safety margin option on the forecast (e.g. use 80% of it).
- Zero-export or export-limited installations: an option for the maximum export
  power; surplus beyond it is curtailed or must be stored.
- Negative prices: exporting at a negative price should be avoided (store or
  curtail), which the cost model handles once the export power limit exists.
- Units and clamping for every picked sensor, as done for the SoC and capacity.
- Tests: with no solar and no load the result equals today's plan; the gain is
  never below zero; a night-time load reduces the energy planned for export; a
  sunny forecast lowers the energy bought to charge; the energy balance (grid +
  solar = load + battery change) holds in every interval; the consumption
  profile learned from statistics matches a hand-made series.

Build this after items 4 and 6 (it reuses the state-of-charge curve) and before
the custom cards (item 7), so the cards can show the house reserve too.

## Price sources besides an entity

Today the price source is a sensor that follows the contract. Add:

- **Fixed prices:** one buy and one sell price, or a time-of-use schedule
  (periods with their own prices), for users without a spot-price source. It
  matters most together with item 8 (solar and consumption), where the value of
  stored energy is the buy price avoided.
- **Adapters** for popular sources that use another attribute layout (for
  example Nord Pool's `raw_today` / `raw_tomorrow`), with a tariff step (formula
  or simple fields) for sources that only give the spot price.
