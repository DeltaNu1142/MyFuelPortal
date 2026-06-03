"""MyFuelPortal integration setup/teardown."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

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

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor"]


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
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry (called when options change)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down the entry and drop its coordinator."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
