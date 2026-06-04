"""MyFuelPortal sensors.

Two flavours of sensor live here:

  * STATELESS sensors (gallons, level, capacity, last delivery, reading date) just
    read one field out of the latest scraped ``TankData``. They're defined
    declaratively in the ``TANK_SENSORS`` table — adding a field is one row, not a
    new class — and all share the single ``MyFuelPortalTankSensor`` implementation.

  * COMPUTED sensors (daily usage, cumulative usage) need memory of the previous
    reading, so they keep bespoke classes and persist their state across restarts
    with ``RestoreSensor``. ``TankCumulativeUsageSensor`` is the one that feeds the
    HA Energy dashboard's Gas section (device_class gas / total_increasing / ft³).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import AccountData, DeliveryData, TankData
from .const import DOMAIN, GALLONS_TO_CUBIC_FEET
from .coordinator import MyFuelPortalCoordinator


# --- Small helpers -----------------------------------------------------------
def _slug(tank_name: str) -> str:
    """Lowercase, underscore-joined tank name for a stable unique_id segment."""
    return tank_name.lower().replace(" ", "_")


def _account_device_info(entry: ConfigEntry) -> DeviceInfo:
    """The account-level device (the 'hub'); tanks nest under it via_device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="MyFuelPortal Account",
        manufacturer="MyFuelPortal",
        model="Account",
    )


def _device_info(entry: ConfigEntry, tank_name: str) -> DeviceInfo:
    """One HA device per tank, nested under the account device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{tank_name}")},
        name=f"Propane Tank: {tank_name}",
        manufacturer="MyFuelPortal",
        model="Monitored Tank",
        via_device=(DOMAIN, entry.entry_id),
    )


def _tank_data(coordinator: MyFuelPortalCoordinator, tank_name: str) -> TankData | None:
    """Find the latest TankData for a named tank, or None if it's gone."""
    for tank in coordinator.data.get("tanks", []):
        if tank["name"] == tank_name:
            return tank
    return None


def _deliveries_for_tank(
    coordinator: MyFuelPortalCoordinator, tank_name: str
) -> list[DeliveryData]:
    """Deliveries for a tank, newest first (rows without a date sort last).

    The delivery 'Tank' cell matches the tank's name from /Tank (e.g.
    'TANK 1: 500G #...'), so we filter on that.
    """
    matching = [
        delivery
        for delivery in coordinator.data.get("deliveries", [])
        if delivery.get("tank") == tank_name
    ]
    matching.sort(key=lambda delivery: delivery.get("date") or "", reverse=True)
    return matching


def _parse_iso_date(value: str | None) -> date | None:
    """Parse the ISO 'YYYY-MM-DD' that api.py stores into a date (or None)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except (ValueError, TypeError):
        return None


def _coerce_float(value: object) -> float | None:
    """Best-effort float() over a restored state/attribute of unknown type."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


# --- Stateless sensors: one description per field ----------------------------
@dataclass(frozen=True, kw_only=True)
class TankSensorDescription(SensorEntityDescription):
    """Describes a stateless tank sensor; ``value_fn`` pulls its value from TankData."""

    value_fn: Callable[[TankData], StateType | date]


TANK_SENSORS: tuple[TankSensorDescription, ...] = (
    TankSensorDescription(
        key="gallons",
        name="Gallons",
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:propane-tank",
        value_fn=lambda tank: tank["gallons"],
    ),
    TankSensorDescription(
        key="percent",
        name="Level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        value_fn=lambda tank: tank["percent"],
    ),
    TankSensorDescription(
        key="capacity",
        name="Capacity",
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:propane-tank-outline",
        value_fn=lambda tank: tank["capacity"],
    ),
    TankSensorDescription(
        key="last_delivery",
        name="Last Delivery",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:truck-delivery",
        value_fn=lambda tank: _parse_iso_date(tank["last_delivery"]),
    ),
    TankSensorDescription(
        key="reading_date",
        name="Reading Date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
        value_fn=lambda tank: _parse_iso_date(tank["reading_date"]),
    ),
)


# --- Delivery-derived sensors: read the tank's deliveries (newest first) ------
def _latest_price_per_cubic_foot(deliveries: list[DeliveryData]) -> float | None:
    """$/ft³ from the most recent delivery, for the Energy gas 'current price'.

    Derived from the delivered $/gal so it matches the consumption unit (ft³):
    $/ft³ = $/gal ÷ (gallons per ft³).
    """
    if not deliveries:
        return None
    price_per_gallon = deliveries[0]["price_per_gallon"]
    if price_per_gallon is None:
        return None
    return round(price_per_gallon / GALLONS_TO_CUBIC_FEET, 4)


def _avg_daily_usage(deliveries: list[DeliveryData]) -> float | None:
    """Average gal/day over the most recent delivery cycle.

    Uses the latest NON-zero delivery's gallons ÷ days since the delivery before
    it — i.e. what was consumed over the period that delivery refilled. Deliveries
    are newest-first. This is steadier than the day-to-day tank-level deltas (and
    is populated from history, instead of waiting for the level to drop). Skips
    zero-gallon "no-fill" visits; None if a cycle can't be formed.
    """
    for i in range(len(deliveries) - 1):
        gallons = deliveries[i]["gallons"]
        if not gallons or gallons <= 0:
            continue
        this_date = _parse_iso_date(deliveries[i]["date"])
        prev_date = _parse_iso_date(deliveries[i + 1]["date"])
        if this_date is None or prev_date is None:
            continue
        days = (this_date - prev_date).days
        if days > 0:
            return round(gallons / days, 2)
    return None


@dataclass(frozen=True, kw_only=True)
class DeliverySensorDescription(SensorEntityDescription):
    """A delivery-derived sensor; ``value_fn`` reads the tank's delivery list."""

    value_fn: Callable[[list[DeliveryData]], StateType]


DELIVERY_SENSORS: tuple[DeliverySensorDescription, ...] = (
    DeliverySensorDescription(
        key="last_delivery_cost",
        name="Last Delivery Cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash",
        value_fn=lambda ds: ds[0]["cost"] if ds else None,
    ),
    DeliverySensorDescription(
        key="last_delivery_gallons",
        name="Last Delivery Gallons",
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        device_class=SensorDeviceClass.VOLUME,
        icon="mdi:propane-tank",
        value_fn=lambda ds: ds[0]["gallons"] if ds else None,
    ),
    # Price sensors carry NO `monetary` device class on purpose — that's reserved
    # for total-cost entities and would hide these from the Energy "current price"
    # picker. `measurement` state class lets you graph price over time.
    DeliverySensorDescription(
        key="price_per_gallon",
        name="Price per Gallon",
        native_unit_of_measurement="USD/gal",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash",
        value_fn=lambda ds: ds[0]["price_per_gallon"] if ds else None,
    ),
    DeliverySensorDescription(
        key="price_per_cubic_foot",
        name="Price per Cubic Foot",
        native_unit_of_measurement="USD/ft³",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash",
        value_fn=_latest_price_per_cubic_foot,
    ),
    # Totals over the fetched history (a wide date range = effectively lifetime).
    DeliverySensorDescription(
        key="total_delivered_gallons",
        name="Total Delivered Gallons",
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:propane-tank",
        value_fn=lambda ds: round(sum(d["gallons"] or 0 for d in ds), 3) if ds else None,
    ),
    DeliverySensorDescription(
        key="total_spend",
        name="Total Spend",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:cash-multiple",
        value_fn=lambda ds: round(sum(d["cost"] or 0 for d in ds), 2) if ds else None,
    ),
    # Usage estimated from deliveries (gal/day) — steadier than the tank-level
    # method and populated from history rather than waiting for the level to drop.
    DeliverySensorDescription(
        key="average_daily_usage",
        name="Average Daily Usage",
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fire",
        value_fn=_avg_daily_usage,
    ),
)


# --- Account-level sensors: one set per account, on the account device --------
@dataclass(frozen=True, kw_only=True)
class AccountSensorDescription(SensorEntityDescription):
    """An account-level sensor; ``value_fn`` reads the AccountData dict."""

    value_fn: Callable[[AccountData], StateType | date]


ACCOUNT_SENSORS: tuple[AccountSensorDescription, ...] = (
    AccountSensorDescription(
        key="customer_since",
        name="Customer Since",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-account",
        value_fn=lambda a: _parse_iso_date(a["customer_since"]),
    ),
    AccountSensorDescription(
        key="account_balance",
        name="Account Balance",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash",
        value_fn=lambda a: a["account_balance"],
    ),
    AccountSensorDescription(
        key="status",
        name="Account Status",
        icon="mdi:account-check",
        value_fn=lambda a: a["status"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one device-worth of sensors per tank found at first refresh."""
    coordinator: MyFuelPortalCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Only add the delivery/price sensors if the delivery page was reachable, so a
    # provider that doesn't expose it doesn't get a row of always-unavailable ones.
    deliveries_available = coordinator.data.get("deliveries_available", False)

    entities: list[SensorEntity] = []
    for tank in coordinator.data.get("tanks", []):
        tank_name = tank["name"]
        entities.extend(
            MyFuelPortalTankSensor(coordinator, entry, tank_name, description)
            for description in TANK_SENSORS
        )
        entities.append(TankDailyUsageSensor(coordinator, entry, tank_name))
        entities.append(TankCumulativeUsageSensor(coordinator, entry, tank_name))
        # Always present — falls back to the manual price when no delivery exists.
        entities.append(MyFuelPortalEffectivePriceSensor(coordinator, entry, tank_name))
        if deliveries_available:
            entities.extend(
                MyFuelPortalDeliverySensor(coordinator, entry, tank_name, description)
                for description in DELIVERY_SENSORS
            )

    # Account-level sensors (once per entry), only if the home page was reachable.
    if coordinator.data.get("account_available"):
        entities.extend(
            MyFuelPortalAccountSensor(coordinator, entry, description)
            for description in ACCOUNT_SENSORS
        )
    async_add_entities(entities)


class MyFuelPortalTankSensor(
    CoordinatorEntity[MyFuelPortalCoordinator], SensorEntity
):
    """A stateless tank sensor driven by a TankSensorDescription."""

    entity_description: TankSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        tank_name: str,
        description: TankSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._tank_name = tank_name
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{_slug(tank_name)}_{description.key}"
        )
        self._attr_device_info = _device_info(entry, tank_name)

    @property
    def native_value(self) -> StateType | date:
        tank = _tank_data(self.coordinator, self._tank_name)
        if tank is None:
            return None
        return self.entity_description.value_fn(tank)


class MyFuelPortalDeliverySensor(
    CoordinatorEntity[MyFuelPortalCoordinator], SensorEntity
):
    """A delivery-derived sensor (last delivery cost/gallons, derived price)."""

    entity_description: DeliverySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        tank_name: str,
        description: DeliverySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._tank_name = tank_name
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{_slug(tank_name)}_{description.key}"
        )
        self._attr_device_info = _device_info(entry, tank_name)

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(
            _deliveries_for_tank(self.coordinator, self._tank_name)
        )


class MyFuelPortalAccountSensor(
    CoordinatorEntity[MyFuelPortalCoordinator], SensorEntity
):
    """An account-level sensor (customer since, balance, status)."""

    entity_description: AccountSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        description: AccountSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_account_{description.key}"
        self._attr_device_info = _account_device_info(entry)

    @property
    def native_value(self) -> StateType | date:
        account = self.coordinator.data.get("account")
        if not account:
            return None
        return self.entity_description.value_fn(account)


class MyFuelPortalEffectivePriceSensor(
    CoordinatorEntity[MyFuelPortalCoordinator], SensorEntity
):
    """$/gal — the latest DELIVERED price when available, else the manual override.

    The batteries-included "derived-else-manual" pattern: for providers that
    expose delivery cost it tracks the real price; otherwise it follows the
    Manual Price per Gallon number entity. The `source` attribute says which.
    """

    _attr_has_entity_name = True
    _attr_name = "Effective Price per Gallon"
    _attr_native_unit_of_measurement = "USD/gal"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:cash-check"

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        tank_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._tank_name = tank_name
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{_slug(tank_name)}_effective_price_per_gallon"
        )
        self._attr_device_info = _device_info(entry, tank_name)

    def _delivered_price(self) -> float | None:
        deliveries = _deliveries_for_tank(self.coordinator, self._tank_name)
        return deliveries[0]["price_per_gallon"] if deliveries else None

    @property
    def native_value(self) -> StateType:
        delivered = self._delivered_price()
        if delivered is not None:
            return delivered
        return self.coordinator.manual_price_per_gallon

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "source": "delivery" if self._delivered_price() is not None else "manual"
        }


class TankDailyUsageSensor(
    CoordinatorEntity[MyFuelPortalCoordinator], RestoreSensor
):
    """Estimated gallons/day between consecutive tank readings.

    Computing a daily rate needs the PREVIOUS reading. Upstream kept that in plain
    instance attributes, so a restart wiped it and the sensor returned None until
    two fresh readings accrued. Here the previous reading (gallons + date) is
    stashed in extra state attributes and restored on startup, so the estimate
    survives restarts.
    """

    _attr_has_entity_name = True
    _attr_name = "Daily Usage"
    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:fire"

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        tank_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._tank_name = tank_name
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{_slug(tank_name)}_daily_usage"
        )
        self._attr_device_info = _device_info(entry, tank_name)
        self._prev_gallons: float | None = None
        self._prev_date: date | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_native_value = _coerce_float(last.state)
            self._prev_gallons = _coerce_float(last.attributes.get("prev_gallons"))
            prev_date = last.attributes.get("prev_date")
            self._prev_date = _parse_iso_date(prev_date if isinstance(prev_date, str) else None)
        # Fold in anything the coordinator already has.
        self._recompute()

    @property
    def extra_state_attributes(self) -> dict[str, str | float | None]:
        """Persist the previous reading so the delta survives a restart."""
        return {
            "prev_gallons": self._prev_gallons,
            "prev_date": self._prev_date.isoformat() if self._prev_date else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self._recompute()
        self.async_write_ha_state()

    def _recompute(self) -> None:
        tank = _tank_data(self.coordinator, self._tank_name)
        if tank is None:
            return
        gallons = tank["gallons"]
        reading = _parse_iso_date(tank["reading_date"])
        if gallons is None or reading is None:
            return

        if self._prev_date is None:
            # First reading we've seen — set the baseline, no usage yet.
            self._prev_gallons = gallons
            self._prev_date = reading
        elif reading > self._prev_date:
            # Only advance on a NEW reading date; repeated polls within the same
            # day must not corrupt the baseline.
            if self._prev_gallons is not None:
                days = (reading - self._prev_date).days
                if days > 0 and self._prev_gallons > gallons:
                    self._attr_native_value = round(
                        (self._prev_gallons - gallons) / days, 2
                    )
            self._prev_gallons = gallons
            self._prev_date = reading


class TankCumulativeUsageSensor(
    CoordinatorEntity[MyFuelPortalCoordinator], RestoreSensor
):
    """Total propane consumed, in cubic feet — feeds the HA Energy Gas section.

    Each poll, a DROP in the gallons reading is treated as consumption (a delivery
    raises the level and is ignored — that's not usage), accumulated and converted
    to ft³ via GALLONS_TO_CUBIC_FEET so the value carries a unit the Energy gas
    dashboard accepts. RestoreSensor persists the running total across restarts.
    """

    _attr_has_entity_name = True
    _attr_name = "Cumulative Usage"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_FEET
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(
        self,
        coordinator: MyFuelPortalCoordinator,
        entry: ConfigEntry,
        tank_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._tank_name = tank_name
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{_slug(tank_name)}_cumulative_usage"
        )
        self._attr_device_info = _device_info(entry, tank_name)
        self._last_level: float | None = None
        self._total = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None:
            restored = _coerce_float(last.native_value)
            if restored is not None:
                self._total = restored
        self._attr_native_value = round(self._total, 2)

    @callback
    def _handle_coordinator_update(self) -> None:
        tank = _tank_data(self.coordinator, self._tank_name)
        if tank is None:
            return
        current = tank["gallons"]
        if current is None:
            return
        if self._last_level is not None and current < self._last_level:
            self._total += (self._last_level - current) * GALLONS_TO_CUBIC_FEET
        self._last_level = current
        self._attr_native_value = round(self._total, 2)
        self.async_write_ha_state()
