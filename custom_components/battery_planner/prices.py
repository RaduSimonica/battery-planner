"""Read a price series from any entity that follows the contract.

The contract (see ``docs/price-series.md`` in the OPCOM PZU repository) is an
attribute ``prices``: a list of ``{start, end, buy, sell}`` with ISO 8601
timestamps (with a UTC offset) and prices per kWh. Bad rows are skipped, so one
broken interval does not stop the plan.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PricePoint:
    start: datetime  # UTC
    end: datetime
    buy: float  # per kWh, what you pay
    sell: float  # per kWh, what you receive


def _time(value: Any) -> datetime | None:
    moment = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if moment is None or moment.tzinfo is None:
        return None  # a time without an offset cannot be placed
    return moment.astimezone(timezone.utc)


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def parse_prices(raw: Any) -> list[PricePoint]:
    """Valid intervals of the ``prices`` attribute, sorted by start."""
    if not isinstance(raw, list):
        return []
    points: list[PricePoint] = []
    skipped = 0
    for row in raw:
        if not isinstance(row, dict):
            skipped += 1
            continue
        start, end = _time(row.get("start")), _time(row.get("end"))
        buy, sell = _price(row.get("buy")), _price(row.get("sell"))
        if start is None or end is None or end <= start or buy is None or sell is None:
            skipped += 1
            continue
        points.append(PricePoint(start, end, buy, sell))
    if skipped:
        _LOGGER.debug("Skipped %d unusable price rows", skipped)
    points.sort(key=lambda p: p.start)
    return points


def currency_of(unit: Any) -> str | None:
    """'RON/kWh' -> 'RON'. None unless it looks like a currency code."""
    code = unit.split("/")[0].strip() if isinstance(unit, str) else ""
    return code if len(code) == 3 and code.isalpha() and code.isupper() else None


def local_day_bounds(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    return start, datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)


class PriceSeries:
    """Known intervals, sorted."""

    def __init__(self, points: Iterable[PricePoint] = ()) -> None:
        self.points = sorted(points, key=lambda p: p.start)
        self._starts = [p.start for p in self.points]

    def __bool__(self) -> bool:
        return bool(self.points)

    def at(self, moment: datetime) -> PricePoint | None:
        idx = bisect_right(self._starts, moment) - 1
        if idx >= 0 and self.points[idx].end > moment:
            return self.points[idx]
        return None

    def window(self, start: datetime, end: datetime) -> list[PricePoint]:
        lo = max(0, bisect_right(self._starts, start) - 1)
        return [p for p in self.points[lo:] if p.start < end and p.end > start]

    def covers(self, start: datetime, end: datetime) -> list[PricePoint] | None:
        """Points of [start, end) if the series spans the whole window, else None.

        Gaps inside the window are allowed (the planner only plans what is
        published), but the window must be entered and left inside the series.
        """
        pts = self.window(start, end)
        if pts and pts[0].start <= start and pts[-1].end >= end:
            return pts
        return None
