"""Gemeinsame Basisklasse der Entitäten."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, is_german


class EnthaertungEntity(CoordinatorEntity[DataUpdateCoordinator]):
    """Entität, die ihre Werte vom Manager (über den Koordinator) bekommt."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DataUpdateCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model=(
                "Regenerations- und Salzüberwachung"
                if is_german(coordinator.hass.config.language)
                else "Regeneration and salt monitoring"
            ),
            entry_type=DeviceEntryType.SERVICE,
        )
