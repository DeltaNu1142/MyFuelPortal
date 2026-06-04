"""MyFuelPortal manual price-override number.

A user-settable $/gal that the effective-price sensor falls back to when the
portal exposes no delivered price. RestoreNumber persists the value across
restarts; it's also published to the coordinator so the sensor can read it
without a cross-entity lookup.
"""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MANUAL_PRICE_PER_GALLON, DOMAIN
from .coordinator import MyFuelPortalCoordinator
from .sensor import _account_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One manual-price override number per account."""
    coordinator: MyFuelPortalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MyFuelPortalManualPriceNumber(coordinator, entry)])


class MyFuelPortalManualPriceNumber(
    CoordinatorEntity[MyFuelPortalCoordinator], RestoreNumber
):
    """Manual $/gal — the price fallback when no delivered price is available."""

    _attr_has_entity_name = True
    _attr_name = "Manual Price per Gallon"
    _attr_native_unit_of_measurement = "USD/gal"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 20.0
    _attr_native_step = 0.001
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:cash-edit"

    def __init__(
        self, coordinator: MyFuelPortalCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_manual_price_per_gallon"
        self._attr_device_info = _account_device_info(entry)
        self._attr_native_value = DEFAULT_MANUAL_PRICE_PER_GALLON

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
        # Publish to the coordinator so the effective-price sensor reads it, and
        # nudge listeners so that sensor recomputes right away.
        self.coordinator.manual_price_per_gallon = self._attr_native_value
        self.coordinator.async_update_listeners()

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.manual_price_per_gallon = value
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
