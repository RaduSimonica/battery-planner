"""Solar production: the forecast from the Energy dashboard, spread over the plan's intervals.

Home Assistant's Energy dashboard knows which solar forecasts you use (Forecast.Solar,
Solcast, Open-Meteo and others register as forecast providers) and each gives
hourly energy. All of them listed on the dashboard are added up, so a house with
several arrays, each with its own forecast, is covered. Nothing here depends on which
provider it is.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from math import exp
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .prices import PricePoint

_LOGGER = logging.getLogger(__name__)

DEFAULT_PERIOD_S = 3600  # forecasts are hourly
FADE_HOURS = 1.0  # a sunnier or cloudier moment than forecast matters for about an hour
DAYLIGHT_KW = 0.2  # below this the forecast is too small to compare with


async def _forecast_entries(hass: HomeAssistant) -> list[str]:
    """Config entries of the solar forecasts the Energy dashboard uses."""
    from homeassistant.components.energy.data import async_get_manager

    manager = await async_get_manager(hass)
    if manager.data is None:
        return []
    found: dict[str, None] = {}
    for source in manager.data.get("energy_sources", []):
        if source.get("type") == "solar":
            for entry_id in source.get("config_entry_solar_forecast") or []:
                found[entry_id] = None
    return list(found)


async def has_forecast(hass: HomeAssistant) -> bool:
    """Whether the Energy dashboard has a solar forecast configured."""
    try:
        return bool(await _forecast_entries(hass))
    except Exception:  # noqa: BLE001 - the energy integration may be missing
        return False


# One piece of forecast: (start, end as epoch seconds, kWh produced in it).
Interval = tuple[float, float, float]

# Providers whose timestamps mark the END of the period (the energy since the previous
# timestamp, with extra stamps at sunrise and sunset). Checked against real data from
# Forecast.Solar: the hours around solar noon are symmetric only that way. The others
# (Solcast, Open-Meteo, ...) follow Home Assistant's convention: the start of the period.
END_ALIGNED = {"forecast_solar"}

MAX_PERIOD_S = 2 * 3600  # a longer gap is the night, not a period


def to_intervals(domain: str, wh_hours: dict[str, float]) -> list[Interval]:
    """The energy each forecast value stands for, as intervals of time."""
    stamps: list[tuple[float, float]] = []
    for text, wh in wh_hours.items():
        try:
            stamps.append((datetime.fromisoformat(text).timestamp(), float(wh)))
        except (ValueError, TypeError):
            continue
    stamps.sort()
    if not stamps:
        return []
    gaps = [b[0] - a[0] for a, b in zip(stamps, stamps[1:]) if 0 < b[0] - a[0] <= MAX_PERIOD_S]
    typical = min(gaps) if gaps else DEFAULT_PERIOD_S
    out: list[Interval] = []
    for i, (ts, wh) in enumerate(stamps):
        if domain in END_ALIGNED:  # the energy since the previous stamp
            before = stamps[i - 1][0] if i else None
            start = before if before is not None and 0 < ts - before <= MAX_PERIOD_S else ts - typical
            out.append((start, ts, wh / 1000))
        else:  # the energy from this stamp to the next
            after = stamps[i + 1][0] if i + 1 < len(stamps) else None
            end = after if after is not None and 0 < after - ts <= MAX_PERIOD_S else ts + typical
            out.append((ts, end, wh / 1000))
    return out


async def fetch_forecast(hass: HomeAssistant) -> list[Interval]:
    """The solar forecast of every provider on the Energy dashboard, as intervals (all added up)."""
    try:
        from homeassistant.components.energy.websocket_api import async_get_energy_platforms

        entries = await _forecast_entries(hass)
        if not entries:
            return []
        platforms = await async_get_energy_platforms(hass)
    except Exception:  # noqa: BLE001 - no energy integration, nothing to add
        _LOGGER.debug("The Energy dashboard is not available", exc_info=True)
        return []
    intervals: list[Interval] = []
    for entry_id in entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain not in platforms:
            continue
        try:
            forecast = await platforms[entry.domain](hass, entry_id)
        except Exception:  # noqa: BLE001 - one provider failing must not stop the others
            _LOGGER.debug("Solar forecast of %s failed", entry.domain, exc_info=True)
            continue
        intervals += to_intervals(entry.domain, (forecast or {}).get("wh_hours") or {})
    return intervals


def slot_kwh(points: Sequence[PricePoint], forecast: list[Interval]) -> list[float]:
    """Expected production (kWh) in each interval: each piece of forecast spread evenly over its time."""
    if not forecast:
        return [0.0] * len(points)
    out = []
    for p in points:
        a, b = p.start.timestamp(), p.end.timestamp()
        total = 0.0
        for begin, end, kwh in forecast:
            overlap = min(b, end) - max(a, begin)
            if overlap > 0 and end > begin:
                total += kwh * overlap / (end - begin)
        out.append(total)
    return out


def follow_live(
    energies: list[float], points: Sequence[PricePoint], hours: float, now: datetime, live_kw: float | None
) -> list[float]:
    """Bend the next hour or so towards what the panels make right now.

    In daylight the forecast is scaled by how much sunnier or cloudier it is than
    forecast (at most twice as much), fading over an hour. Before the forecast expects
    any sun, what the panels already make is added.
    """
    if live_kw is None or not energies:
        return energies
    expected_kw = energies[0] / hours
    out = []
    for energy, p in zip(energies, points):
        ahead = max((p.start - now).total_seconds() / 3600, 0.0)
        fade = exp(-ahead / FADE_HOURS)
        if expected_kw >= DAYLIGHT_KW:
            ratio = min(max(live_kw / expected_kw, 0.0), 2.0)
            out.append(max(energy * (1 + (ratio - 1) * fade), 0.0))
        else:
            out.append(max(energy + (live_kw - expected_kw) * hours * fade, 0.0))
    return out
