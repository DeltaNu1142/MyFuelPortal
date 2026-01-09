"""MyFuelPortal integration."""
DOMAIN = "myfuelportal"

async def async_setup(hass, config):
    return True

async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True

async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])
