"""The planning mode, chosen from a dashboard or an automation."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .arbitrage import MODES
from .coordinator import PlannerCoordinator
from .entity import PlannerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ModeSelect(entry.runtime_data)])


class ModeSelect(PlannerEntity, SelectEntity, RestoreEntity):
    """Max profit, cheap self-consumption or balanced. Remembered across restarts."""

    _attr_options = list(MODES)

    def __init__(self, coordinator: PlannerCoordinator) -> None:
        super().__init__(coordinator, "mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in MODES:
            await self.coordinator.async_set_mode(last.state)

    @property
    def current_option(self) -> str:
        return self.coordinator.mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option)
        self.async_write_ha_state()
