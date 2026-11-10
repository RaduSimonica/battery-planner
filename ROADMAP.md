# Roadmap

Planned improvements, not implemented yet. Each ships as a version of its own
(see [CONTRIBUTING.md](CONTRIBUTING.md)). Prices come from other integrations
through the [price series contract](https://github.com/RaduSimonica/ha-opcom-pzu/blob/main/docs/price-series.md);
the OPCOM PZU roadmap is in its own repository.

## 1. [Battery Planner, price card in OPCOM PZU] Custom dashboard card(s)

apexcharts-card works but is not the nicest to look at, and each example needs
a long block of YAML. A card made for this integration would look better and be
one line to add.

- **Battery card**: a battery shape showing the current level (from the SoC
  sensor) and the projected level at the end of the day, with what the plan does
  now (charging, exporting, idle), the energy to charge and export (the energy sensors) and
  the next change. Colours follow the theme.
- **Price card**: the merged price chart of OPCOM PZU (spot, buy, sell, now
  marker, cheap and expensive windows shaded, the planned charge and discharge
  windows marked), instead of an apexcharts configuration.
- Delivery: a single JavaScript file in the repository, registered as a
  dashboard resource by the integration so HACS users do not add it by hand
  (or documented as one step if that is not possible). Cards take the entity
  from their configuration and read the existing attributes, so no new backend
  work is needed beyond the energy and state-of-charge sensors. Needs a visual editor for the card
  options, a preview in the card picker, and light and dark theme checks.
- Decide first whether it is one card with options or two, and whether to build
  on Lit (as Home Assistant does) or plain web components. Keep the apexcharts
  examples as the fallback.

## 2. [Battery Planner] Planning modes

Different people want different things from the battery, so the plan follows a
mode, chosen in a **select entity** (so a dashboard or an automation can switch it
at any time):

- **Max profit** (today's behaviour, the default): only buys cheap and sells
  dear. The house is ignored; the battery is a trading asset.
- **Cheap self-consumption**: the battery exists to power the house at the
  lowest cost. It learns when the house uses energy and charges, in the cheapest
  intervals, enough for the next period of consumption. It does not export for
  profit. The value of a stored kWh is the *buy* price it avoids. Open
  question, to be settled with real data: fill the battery, or only enough to last
  until the sun is up again.
- **Balanced**: cover the house first, then **sell the surplus**, but only when
  enough energy is left to last until the next sunny period. Do not buy from the
  grid when the sun will fill the battery (summer).

**Goals behind the modes**
- Sunny days, maximum profit: fill the battery from the sun when the export price
  is low, export when it is highest (early morning, late afternoon, sometimes at night).
- No sun and high consumption (winter, heat pump, a fairly flat load over 24 h):
  buy when it is cheap, consume when it is dear, and sell when it is dear if enough
  is left.

How it fits the planner: the modes are the same dynamic programme with a
different value for a kWh leaving the battery and a different rule for what may be
exported (see item 3 for the model). They need the consumption forecast and,
for the sunny-day goals, the solar forecast, so build them together; max profit
works without either. Tests: in max profit mode nothing changes; in
self-consumption mode nothing is exported; balanced exports only what is above the
energy needed until the next sun.

**Reporting.** Show *earnings* (sold energy), *expenses* (bought energy) and *net
gain* as separate sensors, for today and tomorrow, plus a `mode` attribute. More
data is better; remove what proves noisy.

## 3. [Battery Planner] Solar and house consumption in the plan

Take the user's solar production, consumption and solar forecast into account,
so the planner knows how much of the battery can really be sold, and does not
sell energy the house will need later and buy back at a higher price.

**What this is for.** Most users of a battery also have solar panels. The
planner should charge the battery from the solar surplus and **avoid exporting
to the grid when the price is low**, store that energy, and sell it later when the
price is higher (or use it in the house when buying would cost more). Surplus
solar costs nothing to store, so it is the cheapest energy the battery can get.
Grid charging then only fills what solar will not.

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
  "reserve for the house" is an outcome, not a setting. The usable share
  stays as a manual floor on top of it.
- Tomorrow's plan can start from today's *projected* end-of-day level instead of
  an empty battery (the projected state of charge sensors provide it), which is a better assumption once
  solar and load are known.

**Inputs (all optional, all existing sensors the user picks):**
- *Solar production now* (W or kW) and *house consumption now* (W or kW), used
  for the current interval and to correct the forecast. Consumption is read in one
  of two ways: **one grid power sensor** that is positive and negative (with a *flip
  the sign* option, as some meters report export as positive), or **two sensors**,
  one for import and one for export. House consumption is then grid power plus solar
  minus battery power.
- The plan stays on the prices' **15-minute intervals**. Consumption is learned
  per hour (from the recorder's hourly statistics) and spread evenly over the
  four intervals of the hour. An **EV charger** sensor can be left out of the
  house's consumption (done).
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
projected state-of-charge curve including solar and load.

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

**Progress.** Slice 1 (house power from the grid, solar, battery and EV sensors, and the consumption forecast learned from the recorder's hourly statistics) is in snapshot 0.3.0-snapshot.1. Slice 2 (the cost model with the house, the mode select, earnings and expenses) is in snapshot 0.3.0-snapshot.2. Next: solar (slice 3).

The *Plan* sensor now carries everything per interval (see the README), so the cards of item 1 need no new backend work; solar will add its own columns to it.

Build this together with the modes (item 2) and before the custom cards (item 1), so the cards can show the house reserve too.

## Price sources besides an entity

Today the price source is a sensor that follows the contract. Add:

- **Fixed prices:** one buy and one sell price, or a time-of-use schedule
  (periods with their own prices), for users without a spot-price source. It
  matters most together with item 3 (solar and consumption), where the value of
  stored energy is the buy price avoided.
- **Adapters** for popular sources that use another attribute layout (for
  example Nord Pool's `raw_today` / `raw_tomorrow`), with a tariff step (formula
  or simple fields) for sources that only give the spot price.
