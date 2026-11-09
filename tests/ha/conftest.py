"""Shared fixtures: a fake price source that follows the contract."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_planner.const import CONF_PRICE_ENTITY, DOMAIN

SOURCE = "sensor.prices"
TZ_OFFSET = timedelta(hours=2)  # Europe/Bucharest in November 2026 (EET)


@pytest.fixture(autouse=True)
async def _setup(enable_custom_integrations, hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/Bucharest")


def rows(
    first: str = "2026-11-09T00:00:00+02:00",
    days: float = 2,
    step: int = 15,
    cheap=(2, 5),
    dear=(18, 21),
    low: float = 0.3,
    high: float = 2.0,
    mid: float = 0.9,
    since: str | None = None,
) -> list[dict]:
    """A price series: cheap at night, dear in the evening. Local hours, UTC+2."""
    start = datetime.fromisoformat(first)
    count = int(days * 24 * 60 / step)
    cut = datetime.fromisoformat(since) if since else None
    out = []
    for i in range(count):
        s = start + timedelta(minutes=step * i)
        if cut and s + timedelta(minutes=step) <= cut:
            continue  # past intervals are not published, like the OPCOM sensor
        hour = s.hour
        price = low if cheap[0] <= hour < cheap[1] else high if dear[0] <= hour < dear[1] else mid
        out.append(
            {
                "start": s.isoformat(),
                "end": (s + timedelta(minutes=step)).isoformat(),
                "buy": price * 1.21,
                "sell": price,
                "spot": price,
            }
        )
    return out


def publish(hass: HomeAssistant, prices: list[dict], state: str | None = None, unit: str = "RON/kWh") -> None:
    hass.states.async_set(SOURCE, state or str(len(prices)), {"unit": unit, "prices": prices})


@pytest.fixture
def entry(hass: HomeAssistant):
    e = MockConfigEntry(domain=DOMAIN, data={CONF_PRICE_ENTITY: SOURCE}, options={})
    e.add_to_hass(hass)
    return e


async def setup(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


NOW = "2026-11-09 12:00:30+02:00"
