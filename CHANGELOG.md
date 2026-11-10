# Changelog

Pull requests into `staging` add a snapshot section (`## [X.Y.Z-snapshot.N]`) and
publish a pre-release; the pull request into `main` replaces them with one
`## [X.Y.Z]` section and publishes the stable release that HACS shows as the
version. See [CONTRIBUTING.md](CONTRIBUTING.md). Versions follow
[semantic versioning](https://semver.org): `0.x.0` for new features, `0.x.y`
for fixes.

## [0.3.0-snapshot.3] - 2026-11-10

### Added
- **Solar.** The plan knows what the panels make. The forecast comes from your Energy dashboard: every solar forecast listed there (Forecast.Solar, Solcast, Open-Meteo and others) is used and added up, so several arrays with a forecast each are covered. Production first covers the house; the surplus can charge the battery for free, or be exported. The plan stores sun that would otherwise be exported cheaply and sells it later when the price is high; if the battery is full the surplus is exported anyway. It does not export at a negative sell price (the surplus is stored or curtailed). A negative buy price would be charged from the grid, though supplier plans rarely produce one. Surplus beyond the grid export limit is curtailed. Solar is part of the *Balanced* and *Cheap self-consumption* modes (max profit still ignores the house and the panels).
- **Export limit.** The battery's *Maximum discharge power* also stands for the grid export limit: enter the lower of the battery's limit and the limit you are allowed.
- **Free energy** binary sensor: on while energy costs nothing or would be wasted (a buy price of zero or less, or solar surplus the battery does not take that would be exported for nothing or curtailed), for automations that run heavy loads such as a heat pump. Attributes: `reason`, `until`, `next_start`, `next_end` and the `windows` ahead.
- **Several solar production sensors** can be picked on *House consumption*; they are added up. The live production shapes the next hour (a cloudier or sunnier moment than forecast), and the plan is redone early when it differs from the forecast by 0.75 kW or more.
- **Expected solar today / tomorrow** sensors (when a forecast exists). The *Plan* rows gain `solar_kwh`, `solar_to_battery_kwh`, `grid_import_kwh`, `grid_export_kwh` and `curtailed_kwh`; charge windows gain `from_solar_kwh`; the net gain attributes gain `energy_from_solar_kwh` and `solar_curtailed_kwh`.
- **Plan** sensor: the plan interval by interval, for charts and cards. Its `plan` attribute has one row per interval from now to the end of tomorrow: buy and sell price, the action, energy bought, delivered, to the house and exported, the house's expected use, the battery level (kWh and %) and the running earnings, expenses and net result. Three ready-made apexcharts cards use it (`examples/dashboards/`): `battery-plan.yaml` (actions over the prices), `battery-soc.yaml` (level, energy moved and house use) and `battery-gain.yaml` (running earnings and expenses).
- **The plan follows what is happening.** The live house power, averaged over about ten minutes, shapes the next hour or two and fades back to the usual pattern, so an oven or a washing machine that is on now counts. The plan is also redone ahead of the 15-minute step when the house uses 0.75 kW more or less than assumed, or when the battery holds 1 kWh more or less than expected (at most every five minutes). The `house_load_shift_kw` attribute shows the correction.
- **Net result today / tomorrow** sensors: the net gain plus the change in the value of the energy left in the battery. In the house modes the plan runs the battery down, and refilling it is paid for the next day; the net result shows that cost on the day it happens, so it is the figure to compare the modes with. The net gain stays the cash figure (earnings minus expenses minus battery wear).

### Changed
- **Tomorrow's plan starts where today's plan leaves the battery** (it used to start at the reserve), so the level continues across midnight. The `starting_energy` attribute says so.
- The expected house use is worked out in every mode (for the charts), though only the house modes plan with it.
- The plan is made again as soon as the consumption forecast has been learned after a start, instead of at the next 15-minute step.

## [0.3.0-snapshot.2] - 2026-11-10

Second slice of the planning modes: the plan can include the house.

### Added
- **Planning mode** select: *Max profit* (as before, the default), *Cheap self-consumption* (the battery covers the house and never exports, worth the buy price it avoids) and *Balanced* (covers the house first, then exports what is left). It is remembered across restarts and can be changed from an automation.
- The house modes use the learned consumption forecast. Without one the plan falls back to max profit and says so in the `mode_note` attribute.
- **Earnings today / tomorrow** and **Expenses today / tomorrow** sensors next to the net gain. Earnings are the money received for exports plus the purchases avoided at home; the net gain is earnings minus expenses minus battery wear. New attributes: `earnings`, `expenses`, `degradation_cost`, `stored_energy_value`, `energy_to_house_kwh`, `mode`, `expected_consumption_kwh`.
- Energy left in the battery at the end of the plan is valued at what it would cost to buy again, in the house modes, so the plan does not empty the battery just because the day ends.

### Changed
- *Net gain per kWh* is per kWh the battery delivers (to the grid or the house). In max profit mode that is the same as before.

## [0.3.0-snapshot.1] - 2026-11-09

First slice of the planning modes: the planner learns how much the house uses. It does not change the plan yet.

### Added
- **House consumption** page in *Configure*. The house's power is read from a sensor that measures it, or worked out from the grid: one signed sensor (with a *flip* option for meters that report export as positive) or two sensors for import and export, optionally with solar production and battery power (positive = charging). Sensors in W or kW.
- **EV charger** (optional): its power is taken out of the house's consumption. It is a large, unpredictable load that is rarely charged from the home battery.
- **Learned pattern from the recorder.** The average per hour of the day over the last four weeks, separately for weekdays and weekends, is built from Home Assistant's long-term statistics of those sensors, so it starts with the history that already exists. The sensors need `state_class: measurement` (most power sensors have it). The integration now depends on the recorder.
- New sensors, only when consumption sensors are set: *House power* (live) and *Expected consumption next 24 hours* (attributes: the hourly `forecast`, `days_learned`, `hours_known`).

## [0.2.1-snapshot.1] - 2026-11-09

### Fixed
- **A battery below its reserve is handled properly.** With a share below 100% and the charge under the reserve (say 25% with 50% reserved), the plan used to start from "nothing to sell" and could count energy that only refills the reserve as sellable. The reserve is now a floor: the plan starts from the real level, may charge at any level, and only sells what is above the floor.
- The projected state of charge now starts at the real level instead of the reserve, and `starting_energy_kwh` is the whole battery's energy (a new `available_kwh` attribute is what can be sold).
- With no charge sensor, and for tomorrow's plan, the battery is assumed to hold only its reserve.

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
