"""Binary sensors for automations."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PlannerCoordinator
from .entity import PlannerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: PlannerCoordinator = entry.runtime_data
    async_add_entities(
        PlannerProfitable(coordinator, f"profitable_{day}", offset)
        for day, offset in (("today", 0), ("tomorrow", 1))
    )


class PlannerProfitable(PlannerEntity, BinarySensorEntity):
    """On when the plan for the day makes a profit."""

    def __init__(self, coordinator: PlannerCoordinator, key: str, offset: int) -> None:
        super().__init__(coordinator, key)
        self._offset = offset

    @property
    def is_on(self) -> bool | None:
        plan = self.coordinator.data.plans.get(self._offset)
        return None if plan is None else plan.profitable
