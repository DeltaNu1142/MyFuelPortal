"""MyFuelPortal integration setup/teardown."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)

from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .coordinator import MyFuelPortalCoordinator
from .statistics import async_import_estimated_consumption

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["number", "sensor"]
SERVICE_BACKFILL_ENERGY = "backfill_energy_statistics"


def _base_url_from_entry(entry: ConfigEntry) -> str:
    """Return the portal base URL for a config entry.

    New entries store CONF_BASE_URL directly. Entries created before vanity-URL
    support stored only a bare CONF_PROVIDER subdomain — derive the base URL from
    it so those keep working without a formal migration.
    """
    if CONF_BASE_URL in entry.data:
        return entry.data[CONF_BASE_URL]
    return f"https://{entry.data[CONF_PROVIDER]}.myfuelportal.com"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a MyFuelPortal account from a config entry."""
    coordinator = MyFuelPortalCoordinator(
        hass,
        _base_url_from_entry(entry),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        # Poll interval comes from the OptionsFlow (falls back to the default
        # until the user changes it).
        scan_interval_hours=entry.options.get(
            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
        ),
    )
    # Do the first fetch before forwarding to platforms so entities are created
    # from real data (and setup fails cleanly if the very first login fails).
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # When the user saves new options, reload the entry so the new interval is
    # picked up. `async_on_unload` ensures the listener is removed on unload.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    _async_register_services(hass)
    return True


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once (idempotent across config entries)."""
    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL_ENERGY):
        return

    async def _handle_backfill(call: ServiceCall) -> ServiceResponse:
        """Estimate historical consumption from delivery history and import it as
        a gas statistic the Energy dashboard can use. Approximate and optional.

        Returns a per-tank summary (statistic id, points imported, date range,
        total ft³) so the result is visible in Developer Tools -> Actions.
        """
        results: list[dict[str, object]] = []
        for coordinator in hass.data.get(DOMAIN, {}).values():
            data = coordinator.data or {}
            deliveries = data.get("deliveries", [])
            for tank in data.get("tanks", []):
                name = tank.get("name")
                if not name:
                    continue
                tank_deliveries = [d for d in deliveries if d.get("tank") == name]
                if not tank_deliveries:
                    continue
                summary = async_import_estimated_consumption(hass, name, tank_deliveries)
                if summary:
                    results.append({"tank": name, **summary})
        _LOGGER.info("Backfilled estimated consumption for %d tank(s)", len(results))
        return {"tanks": results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL_ENERGY,
        _handle_backfill,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry (called when options change)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down the entry and drop its coordinator."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
