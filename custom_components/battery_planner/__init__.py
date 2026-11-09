"""Battery Planner: when to charge and discharge a battery, from any price series."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import PlannerCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

type PlannerConfigEntry = ConfigEntry[PlannerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PlannerConfigEntry) -> bool:
    coordinator = PlannerCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: PlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PlannerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
