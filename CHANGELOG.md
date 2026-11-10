# Changelog

Pull requests into `staging` add a snapshot section (`## [X.Y.Z-snapshot.N]`) and
publish a pre-release; the pull request into `main` replaces them with one
`## [X.Y.Z]` section and publishes the stable release that HACS shows as the
version. See [CONTRIBUTING.md](CONTRIBUTING.md). Versions follow
[semantic versioning](https://semver.org): `0.x.0` for new features, `0.x.y`
for fixes.

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
