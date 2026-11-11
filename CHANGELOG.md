# Changelog

Pull requests into `staging` add a snapshot section (`## [X.Y.Z-snapshot.N]`) and
publish a pre-release; the pull request into `main` replaces them with one
`## [X.Y.Z]` section and publishes the stable release that HACS shows as the
version. See [CONTRIBUTING.md](CONTRIBUTING.md). Versions follow
[semantic versioning](https://semver.org): `0.x.0` for new features, `0.x.y`
for fixes.

## [0.4.0-snapshot.3] - 2026-11-11

### Fixed
- **The dashboard cards show what the battery does with its energy.** *Battery plan* and *Battery level and energy* drew every discharge as one purple bar, so a battery that only covers the house looked as if it was selling. They now show *Battery to the house* (purple) and *Export to the grid* (pink) separately. Update the cards from `examples/dashboards/`.
- **Balanced no longer sells rounding noise.** With a small house load the planner tracked the battery in steps larger than what the house uses in an interval, so a discharge overshot the load and the excess was exported in a little piece in nearly every interval. The step is now finer.

## [0.4.0-snapshot.2] - 2026-11-11

### Added
- **An EV charger that follows the solar surplus** is part of the plan. Such a charger starts above some surplus (say 4.6 kW) and then takes most of it, up to its maximum (say 11 kW), leaving the battery only a trickle, so on a sunny day the sun the plan counted on for the battery goes into the car. On *House consumption*, next to the EV sensor, set the **start threshold**, the **maximum power** and **what the battery still gets** (1 kW by default), or switch the option off. The planner learns from the recorder how often the car really charged in hours with a big surplus, and expects it to take that share of the surplus before the battery does. A car that is charging now is expected to go on for an hour or two. The *Plan* rows have `ev_kwh`, the net gain `expected_ev_kwh`, the *Plan* sensor `ev_surplus_probability` and `ev_hours_learned`; the *Free energy* signal leaves the car's share out. With fewer than 8 hours of history with a big surplus nothing is subtracted.
- **Expected consumption with EV next 24 hours** sensor, when an EV charger sensor is set: the consumption forecast with the car left in (it stays out of the plan and of *Expected consumption*). Attributes: the hourly `forecast`, `ev_kwh` (the car's share) and `without_ev_kwh`. It is an average of what the car drew at each hour, so it suits the day's total, not predicting a particular night.

## [0.4.0-snapshot.1] - 2026-11-11

### Added
- **Fixed prices** as a price source, for a fixed tariff: a buy price and a sell price per kWh (taxes and VAT included), and an optional **second rate** for some hours (a night rate, for example) with its own buy and sell price, that may run past midnight and applies every day, on weekdays or at weekends. Choose it when adding the integration, or under *Configure → Price source*, where you can also switch back to a price sensor. The planner still stores the sun for later when the buy price is above the sell price, fills the battery at a cheap night rate for the dearer hours, and the *Free energy* signal works when exports earn nothing.

### Changed
- *Price source* is now a choice between a price sensor and fixed prices (a menu in the setup and in *Configure*). Existing installations keep their sensor.

## [0.3.0] - 2026-11-10

Planning modes, solar and the house in the plan. Beta: please report what you find in the issues and attach the diagnostics file.

### Added
- **House consumption** page in *Configure*. The house's power comes from a sensor that measures it, or is worked out from the grid: one signed sensor (with a *flip* option) or two sensors for import and export, optionally with solar production (several sensors are added up), battery power (positive = charging) and an **EV charger** that is left out of the house's consumption.
- **Learned consumption.** The average per hour of the day over the last four weeks, separately for weekdays and weekends, is built from Home Assistant's long-term statistics of those sensors, so it starts with the history that exists. New sensors *House power* and *Expected consumption next 24 hours* (with the hourly `forecast`). The integration now depends on the recorder.
- **Planning mode** select, remembered across restarts and usable from automations: *Max profit* (as before, the default), *Cheap self-consumption* (the battery covers the house and never exports, worth the buy price it avoids) and *Balanced* (covers the house first, then exports what is left). Without a consumption forecast the plan falls back to max profit and says so in the `mode_note` attribute. Energy left in the battery at the end of the plan is valued at what it would cost to buy again, so the plan does not empty the battery just because the day ends.
- **Solar.** The forecast comes from your Energy dashboard: every solar forecast listed there (Forecast.Solar, Solcast, Open-Meteo and others) is used and added up. In the house modes production first covers the house; the surplus can charge the battery for free instead of being exported cheaply, and is sold later when the price is high. Nothing is exported at a negative sell price (the surplus is stored or curtailed), and surplus beyond the grid export limit is curtailed: the battery's *Maximum discharge power* also stands for the export limit, so enter the lower of the battery's limit and the limit you are allowed. *Expected solar today / tomorrow* sensors.
- **Earnings today / tomorrow**, **Expenses today / tomorrow** and **Net result today / tomorrow** sensors next to the net gain. The net gain is earnings minus expenses minus battery wear; the net result adds the change in the value of the energy left in the battery, which makes the house modes comparable with each other.
- **Plan** sensor: the plan interval by interval for charts and cards, from now to the end of tomorrow: prices, the action, energy bought, delivered, to the house and exported, the expected house use and solar, the battery level, and the running earnings, expenses and net result. Three ready-made apexcharts cards in `examples/dashboards/` (apexcharts-card 2.0 or later).
- **The plan follows what is happening.** The live house power and solar production shape the next hour or two and fade back to the usual pattern, so an oven that is on now counts. The plan is also redone ahead of the 15-minute step when the house load or solar production is 0.75 kW off what was assumed, or the battery level is 1 kWh off the plan (at most every five minutes).
- **Free energy** binary sensor for automations that run heavy loads: on while energy costs nothing or would be wasted (a buy price of zero or less, or solar surplus the battery cannot absorb that would be exported for nothing or curtailed). The battery is assumed to charge greedily until full, as an inverter in its normal mode does.
- **Download diagnostics** for bug reports, and issue templates on GitHub.

### Changed
- **Tomorrow's plan starts where today's plan leaves the battery** (it used to start at the reserve), so the level continues across midnight.
- *Net gain per kWh* is per kWh the battery delivers (to the grid or the house). In max profit mode that is the same as before.
- Charge windows include the energy that comes from the sun (`from_solar_kwh`).

### Fixed
- **A battery below its reserve is handled properly.** With a share below 100% and the charge under the reserve (say 25% with 50% reserved), the plan could count energy that only refills the reserve as sellable. The reserve is now a floor: the plan starts from the real level, may charge at any level, and only sells what is above the floor. The projected state of charge starts at the real level, and `starting_energy_kwh` is the whole battery's energy (a new `available_kwh` attribute is what can be sold). With no charge sensor the battery is assumed to hold only its reserve.

## [0.2.0] - 2026-11-09

### Added
- **Share of the battery to use** (battery page): the planner only works with that share and never touches the reserve at the bottom. Only the energy above the reserve counts as available. The plan attributes show `capacity_kwh` (the usable share) and `reserve_kwh`.
- **Energy to charge** and **Energy to export** sensors, today and tomorrow, with the planned windows in the attributes.
- **Projected state of charge** sensors, today and tomorrow: the level at the end of the day if the plan is followed, with the whole curve (and the peak) in the attributes for charts.
- **Time to cheapest price** and **Time to best sell price**: minutes until that interval starts, updated every minute, with a configurable look-ahead (*Timers* page, 24 hours by default).

## [0.1.2] - 2026-11-09

### Fixed
- **The plan is redone as soon as the battery sensors come back.** After a Home Assistant restart the BMS sensor often appears a few seconds after the planner, which then planned for an empty battery until the next 15-minute step. It now plans again when the charge or capacity sensor goes from unavailable to a value.

## [0.1.1] - 2026-11-09

### Fixed
- **A battery sensor in amp-hours (Ah) was read as kWh.** A BMS reporting 300 Ah became a 300 kWh battery and the plan was about 20 times too big. Capacity and state-of-charge sensors are now only used in kWh, Wh (and % for the charge level); any other unit is ignored with a warning and the capacity from the settings is used. Amp-hours cannot be converted without the battery voltage.

## [0.1.0] - 2026-11-09

First release. The battery planner of [OPCOM PZU](https://github.com/RaduSimonica/ha-opcom-pzu)
(up to its 0.2.x), now a separate integration that works with any price source.

### Added
- Reads prices from any sensor that follows the
  [price series format](https://github.com/RaduSimonica/ha-opcom-pzu/blob/main/docs/price-series.md)
  (a `prices` attribute with `start`, `end`, `buy` and `sell` per interval).
- Battery arbitrage planner: capacity, **separate charge and discharge power**, round-trip
  efficiency and degradation cost. It can start from your own state-of-charge and capacity sensors.
- Sensors: net gain today and tomorrow, net gain per kWh, the current action
  (`charge` / `discharge` / `idle`) with the schedule in attributes, and
  profitable-today/tomorrow binary sensors.
- Re-plans when the prices change and every 15 minutes.
