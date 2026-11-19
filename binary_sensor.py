"""MyFuelPortal pending-delivery binary sensor.

One per tank: on when the account's home page currently lists a pending
delivery for that tank. Reuses the account/tank-matching helper already
defined in `sensor.py` (`_pending_delivery_for_tank`) rather than duplicating
the lookup, the same way `number.py` already reuses `_account_device_info`
from there.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MyFuelPortalCoordinator
from .sensor import _device_info, _pending_delivery_for_tank, _slug


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One 'Pending Delivery' binary sensor per tank, if the account page was
    reachable (pending-delivery data comes from there — see `AccountData`)."""
    coordinator: MyFuelPortalCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data.get("account_available"):
        return

    async_add_entities(
        MyFuelPortalPendingDeliveryBinarySensor(coordinator, entry, tank["name"])
        for tank in coordinator.data.get("tanks", [])
    )


class MyFuelPortalPendingDeliveryBinarySensor(
    CoordinatorEntity[MyFuelPortalCoordinator], BinarySensorEntity
):
    """On when the portal currently lists a pending delivery for this tank."""

    _attr_has_entity_name = True
    _attr_name = "Pending Delivery"

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        tank_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._tank_name = tank_name
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{_slug(tank_name)}_pending_delivery"
        )
        self._attr_device_info = _device_info(entry, tank_name)

    @property
    def is_on(self) -> bool:
        return _pending_delivery_for_tank(self.coordinator, self._tank_name) is not None

    @property
    def icon(self) -> str:
        return "mdi:truck-delivery" if self.is_on else "mdi:truck-delivery-outline"
