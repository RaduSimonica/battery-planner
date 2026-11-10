"""House consumption: read it from power sensors and learn the daily pattern.

The house is not measured by one meter in most homes, so its power is worked
out from what is available::

    house = grid + solar - battery charging - EV charging

where *grid* is positive when importing. A sensor that reports the house
directly (for example an inverter's AC consumption) is used as is. An EV charger
is taken out on purpose: it is a large, unpredictable load that almost nobody
charges from the home battery, so it would only spoil the forecast.

The pattern is learned from Home Assistant's own long-term statistics (hourly
means of the sensors), so it starts with the history that already exists. The mean
of a sum is the sum of the means, so the same formula works on the hourly values.
Per hour of the day, and separately for weekdays and weekends, the average of the
last weeks is the forecast. Everything here is plain Python.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

HOUR = 3600
KEEP_DAYS = 35  # statistics read
LEARN_DAYS = 28  # history used for the forecast

# Power units a sensor may use, as a factor to kW.
POWER_UNITS: dict[str, float] = {"W": 0.001, "kW": 1.0, "MW": 1000.0}


@dataclass(frozen=True, slots=True)
class LoadSources:
    """Which sensors tell the house's power."""

    house: str | None = None  # measures the house directly
    grid: str | None = None  # one signed sensor, positive = import (unless flipped)
    grid_flip: bool = False
    grid_import: str | None = None  # or two sensors, both >= 0
    grid_export: str | None = None
    solar: str | tuple[str, ...] | list[str] | None = None  # production, >= 0; several are added up
    battery: str | None = None  # signed, positive = charging (unless flipped)
    battery_flip: bool = False
    ev: str | None = None  # an EV charger, taken out of the house's power

    @property
    def solar_entities(self) -> tuple[str, ...]:
        """The production sensors (one name or a list, added up)."""
        if not self.solar:
            return ()
        return (self.solar,) if isinstance(self.solar, str) else tuple(self.solar)

    @property
    def entities(self) -> list[str]:
        found = (self.house, self.grid, self.grid_import, self.grid_export, *self.solar_entities, self.battery, self.ev)
        return [e for e in dict.fromkeys(found) if e]

    @property
    def configured(self) -> bool:
        """Enough to work out the house's power."""
        return bool(self.house or self.grid or (self.grid_import and self.grid_export))


def house_power_kw(sources: LoadSources, read: Callable[[str], float | None]) -> float | None:
    """The house's power in kW from ``read`` (entity -> kW, or None if unusable).

    None when any sensor that is needed cannot be read: a guess from part of
    the picture would teach the wrong pattern.
    """
    if sources.house:
        value = read(sources.house)
        if value is None:
            return None
        ev = 0.0
        if sources.ev:
            ev = read(sources.ev)
            if ev is None:
                return None
        return max(value - ev, 0.0)
    if sources.grid:
        grid = read(sources.grid)
        if grid is None:
            return None
        grid = -grid if sources.grid_flip else grid
    elif sources.grid_import and sources.grid_export:
        imported, exported = read(sources.grid_import), read(sources.grid_export)
        if imported is None or exported is None:
            return None
        grid = imported - exported
    else:
        return None
    solar = 0.0
    for entity in sources.solar_entities:
        reading = read(entity)
        if reading is None:
            return None
        solar += reading
    charging = 0.0
    if sources.battery:
        charging = read(sources.battery)
        if charging is None:
            return None
        charging = -charging if sources.battery_flip else charging
    ev = 0.0
    if sources.ev:
        ev = read(sources.ev)
        if ev is None:
            return None
    return max(grid + solar - charging - ev, 0.0)


def hourly_kwh(sources: LoadSources, means: dict[str, dict[int, float]]) -> dict[int, float]:
    """Energy the house used per hour (kWh), from each sensor's hourly mean power in kW.

    ``means`` maps an entity to {start of the hour (epoch seconds): mean kW}. An hour
    is only counted when every sensor that is needed has a value for it.
    """
    hours: set[int] = set()
    for entity in sources.entities:
        hours |= set(means.get(entity, {}))
    out: dict[int, float] = {}
    for hour in sorted(hours):
        kw = house_power_kw(sources, lambda e, h=hour: means.get(e, {}).get(h))
        if kw is not None:
            out[hour] = kw  # one hour: kW and kWh are the same number
    return out


def _key(moment: datetime) -> tuple[bool, int]:
    """(is a weekend day, hour of the local day) of a local time."""
    return moment.weekday() >= 5, moment.hour


class LoadProfile:
    """What the house usually uses in each hour, from hourly history."""

    def __init__(self, hourly: dict[int, float] | None = None) -> None:
        self.hourly = hourly or {}
        self._profiles: dict[str, dict[tuple[bool, int], float]] = {}

    def __bool__(self) -> bool:
        return bool(self.hourly)

    def days_learned(self) -> float:
        return round(len(self.hourly) / 24, 1)

    def profile(self, now: float, tz: tzinfo) -> dict[tuple[bool, int], float]:
        """Mean energy per (weekend, hour of day) over the last weeks."""
        cache = f"{int(now // HOUR)}|{tz}"
        if cache not in self._profiles:
            self._profiles = {}  # one entry is enough: the hour changes
            oldest = now - LEARN_DAYS * 86400
            sums: dict[tuple[bool, int], list[float]] = {}
            for hour, kwh in self.hourly.items():
                if oldest <= hour < now:
                    local = datetime.fromtimestamp(hour, timezone.utc).astimezone(tz)
                    sums.setdefault(_key(local), []).append(kwh)
            self._profiles[cache] = {k: sum(v) / len(v) for k, v in sums.items()}
        return self._profiles[cache]

    def expected(self, start: datetime, now: float, tz: tzinfo) -> float | None:
        """Forecast energy (kWh) for the hour beginning at ``start``.

        Same kind of day and hour first; otherwise the other kind of day (a home
        has no weekday history after one weekend); otherwise None.
        """
        profile = self.profile(now, tz)
        weekend, hour = _key(start.astimezone(tz))
        value = profile.get((weekend, hour))
        return value if value is not None else profile.get((not weekend, hour))

    def forecast(
        self, start: datetime, hours: int, now: float, tz: tzinfo
    ) -> list[tuple[datetime, float | None]]:
        """The next ``hours`` hours from the hour that contains ``start``."""
        first = datetime.fromtimestamp(start.timestamp() // HOUR * HOUR, timezone.utc)
        return [
            (moment, self.expected(moment, now, tz))
            for moment in (first + timedelta(hours=i) for i in range(hours))
        ]


# -- an EV charger that follows the solar surplus ---------------------------------

EV_MIN_KW = 1.0  # the car counts as charging above this
EV_MIN_HOURS = 8  # hours with a big surplus needed before the history says anything


def ev_surplus_probability(
    sources: LoadSources, means: dict[str, dict[int, float]], start_kw: float
) -> tuple[float | None, int]:
    """How often the car charged in hours when the surplus was above its start threshold.

    The surplus is the sun minus the house (without the car). Whatever keeps the car from
    charging on a given day (not plugged in, full, the house battery too low) is in the
    answer. Returns (the share of those hours, the number of hours), None for the share when
    there are too few hours to say anything, or when the sources are not enough.
    """
    if not sources.ev or not sources.solar_entities:
        return None, 0
    house = hourly_kwh(sources, means)
    car = means.get(sources.ev, {})
    sun = [means.get(e, {}) for e in sources.solar_entities]
    big = charging = 0
    for hour, house_kw in house.items():
        if hour not in car or any(hour not in s for s in sun):
            continue
        surplus = max(sum(s[hour] for s in sun) - house_kw, 0.0)
        if surplus >= start_kw:
            big += 1
            charging += car[hour] >= EV_MIN_KW
    if big < EV_MIN_HOURS:
        return None, big
    return charging / big, big
