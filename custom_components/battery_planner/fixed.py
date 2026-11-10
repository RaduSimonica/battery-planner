"""Fixed prices: a buy price and a sell price, with an optional second rate for some hours.

For users without a spot price. The planner needs a price for every quarter hour, so the
prices are turned into the same list of intervals a price sensor would give.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any

from .const import (
    CONF_FIXED_BUY,
    CONF_FIXED_SELL,
    CONF_RATE2_BUY,
    CONF_RATE2_DAYS,
    CONF_RATE2_FROM,
    CONF_RATE2_SELL,
    CONF_RATE2_TO,
)
from .prices import PricePoint

STEP = timedelta(minutes=15)
EVERY_DAY, WEEKDAYS, WEEKENDS = "every", "weekdays", "weekends"
DAY_CHOICES = (EVERY_DAY, WEEKDAYS, WEEKENDS)


@dataclass(frozen=True, slots=True)
class SecondRate:
    start: time
    end: time  # if earlier than the start, the rate runs past midnight
    buy: float
    sell: float
    days: str = EVERY_DAY  # the day the interval falls on

    def applies(self, moment: datetime) -> bool:
        weekend = moment.weekday() >= 5
        if (self.days == WEEKDAYS and weekend) or (self.days == WEEKENDS and not weekend):
            return False
        at = moment.time().replace(tzinfo=None)
        if self.start < self.end:
            return self.start <= at < self.end
        return at >= self.start or at < self.end  # past midnight


@dataclass(frozen=True, slots=True)
class FixedPrices:
    buy: float
    sell: float
    second: SecondRate | None = None

    def rate(self, moment: datetime) -> tuple[float, float]:
        """(buy, sell) for the quarter hour starting at the local ``moment``."""
        if self.second and self.second.applies(moment):
            return self.second.buy, self.second.sell
        return self.buy, self.sell


def _time(value: Any) -> time | None:
    try:
        return time.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def from_data(data: Mapping[str, Any]) -> FixedPrices | None:
    """The fixed prices in a config entry's data, None if it does not hold usable ones."""
    try:
        buy, sell = float(data[CONF_FIXED_BUY]), float(data[CONF_FIXED_SELL])
    except (KeyError, TypeError, ValueError):
        return None
    second = None
    start, end = _time(data.get(CONF_RATE2_FROM)), _time(data.get(CONF_RATE2_TO))
    if start is not None and end is not None and start != end:
        try:
            second = SecondRate(
                start, end, float(data[CONF_RATE2_BUY]), float(data[CONF_RATE2_SELL]),
                data.get(CONF_RATE2_DAYS) if data.get(CONF_RATE2_DAYS) in DAY_CHOICES else EVERY_DAY,
            )
        except (KeyError, TypeError, ValueError):
            second = None
    return FixedPrices(buy, sell, second)


def points(prices: FixedPrices, start: datetime, end: datetime, tz: tzinfo) -> Iterator[PricePoint]:
    """Quarter hours from ``start`` to ``end`` (any time zone), priced by their local time."""
    moment = start.astimezone(timezone.utc)
    stop = end.astimezone(timezone.utc)
    while moment < stop:
        buy, sell = prices.rate(moment.astimezone(tz))
        yield PricePoint(moment, moment + STEP, buy, sell)
        moment += STEP
