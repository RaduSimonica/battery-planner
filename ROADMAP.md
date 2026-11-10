# Roadmap

What comes next. Each item ships as a version of its own (see
[CONTRIBUTING.md](CONTRIBUTING.md)). Prices come from other integrations through
the [price series contract](https://github.com/RaduSimonica/ha-opcom-pzu/blob/main/docs/price-series.md);
the OPCOM PZU roadmap is in its own repository.

## Shipped

- **0.1 – 0.2:** the planner, battery options, energy and state-of-charge sensors, countdown timers, usable share.
- **0.3.0 (beta):** house consumption learned from the recorder, the three planning modes
  (max profit, cheap self-consumption, balanced), solar from the Energy dashboard's
  forecasts, earnings, expenses and net result, the *Plan* sensor and example cards, the
  *Free energy* signal, a plan that follows the house and the sun, diagnostics.

## 1. Fixed and time-of-use prices (first version in snapshot 0.4.0-snapshot.1)

Today the price source is a sensor that follows the price series contract. Add a
second kind of source for users without a spot price:

- **Fixed prices:** one buy price and one sell price (taxes and VAT included, per kWh).
- **A second rate** that applies during certain hours (a night rate, for example), every
  day, on weekdays or at weekends. Done. To do: more periods (a weekend rate and a night rate
  together, a peak rate), and public holidays as weekends.
- With flat prices there is nothing to trade, but the planner still stores the sun
  instead of exporting it when the buy price is above the sell price, and the
  *Free energy* signal works when exports earn nothing.

## 2. Custom dashboard cards

apexcharts-card works but is not the nicest to look at, and each example needs a long
block of YAML. A card made for this integration would look better and be one line to add.

- **Battery card:** a battery shape with the current and the planned level, what the plan
  does now (charging, exporting, idle), the energy to charge and export, and the next change.
  Colours follow the theme.
- **Plan chart:** prices, the planned charge and discharge, the level, house use and solar,
  with a hover per interval and the real battery level for the hours that have passed.
- **Price card** (in OPCOM PZU): spot, buy and sell price, a now marker, the cheap and
  expensive windows shaded and the planned windows marked.
- Delivery: a single JavaScript file in the repository, registered as a dashboard resource
  by the integration so HACS users do not add it by hand (or documented as one step if that
  is not possible). Cards read the *Plan* sensor, so no new backend work is needed. A visual
  editor for the options, a preview in the card picker, and light and dark theme checks.
- Decide first whether it is one card with options or two, and whether to build on Lit (as
  Home Assistant does) or plain web components. Keep the apexcharts examples as the fallback.

## 3. Battery limits: reserve and trade share

Today one setting, *Share of the battery to use*, keeps a reserve at the bottom that is
never sold. Split the battery into three parts instead:

- **Reserve:** never touched (backup power, the inverter's own minimum).
- **Trade share:** a percentage of the battery used **only for trading**: buy when it is
  cheap, sell when it is dear, whatever the house needs.
- **House share:** the rest, minus the reserve, used **only for the house**
  (self-consumption, storing the sun), never sold for arbitrage.

For example 15 kWh with a 20% reserve and a 30% trade share: 3 kWh reserve, 4.5 kWh for
trading, 7.5 kWh for the house.

Open questions, to be settled when we build it:
- **Integration with the plan.** Two separate energy levels make the state space of the
  planner two-dimensional, which is too big at today's resolution. A likely way is two coupled
  plans (one per share) that share the charge and discharge power limits, the export cap and
  the solar surplus, solved one after the other with the house share first.
- **Who gets the solar surplus.** House share first, then trade share, or the other way round.
- **Reading the real level.** The state-of-charge sensor gives only the total. The split
  would be an accounting the planner keeps itself, restored after a restart.
- **Interplay with the modes.** In *Max profit* only the trade share matters; the house modes
  use the house share and, for exports, the trade share.
- **Settings.** Percentages of the usable capacity, with the current *Share of the battery to
  use* becoming the reserve (migrating existing settings without a change in behaviour).

## 4. Applying the plan to the inverter

The plan only advises: the *Action* sensor and the planned level are for you and your
automations, and an inverter in its normal mode may do something else (charge from surplus
solar until full, for example). Offer a way to follow the plan: ready-made automations or
blueprints first (for example for Victron ESS settings), then optionally a service that sets
the grid setpoint or the charge and discharge limits for the current interval.

## 5. Better reporting

- **Split *Expenses*** into real purchases and **forgone exports** (the revenue given up
  when the sun is stored), so the figure does not look like money spent.
- **Max profit's net result** is not comparable with the house modes, because that mode
  places no value on the energy left in the battery. Value it for reporting only.
- **Plan against reality:** record what the battery really did and compare it with the plan.
- Sensors the first design listed and we did not build: expected self-consumption, grid import
  avoided, energy usable for arbitrage, and energy kept as house reserve.

## 6. Forecasts

- **Consumption:** today an average per hour for weekdays and weekends. Learn from the
  temperature (a heat pump works harder in the cold) or from a weather forecast.
- **Solar:** a fallback for forecast providers that are not on the Energy dashboard (a sensor
  with a forecast attribute), a safety margin option (use 80% of the forecast), a per-array
  view, and checking providers other than Forecast.Solar against real data.
- An option for systems that cannot curtail surplus solar.
- **An EV charger in the plan:** first version done in 0.4.0 (a charger that follows the surplus takes its learned share of it before the battery does). To do: whether the car really competes with the battery or only with the export in other systems, a charger on a schedule, and learning the start threshold and the battery's trickle from the history instead of asking.

## 7. More price sources

- **Adapters** for popular sources that use another attribute layout (for example Nord Pool's
  `raw_today` / `raw_tomorrow`), with a tariff step (formula or simple fields) for sources that
  only give the spot price.

## 8. Later

- **1.0.0** when the price series contract and the options have stayed stable for a while.
