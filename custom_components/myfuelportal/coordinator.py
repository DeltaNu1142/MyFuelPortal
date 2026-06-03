"""MyFuelPortal data update coordinator.

The coordinator's only jobs are: hold the client, run it on a schedule (off the
event loop, since `requests` is blocking), and translate the client's exceptions
into Home Assistant's `UpdateFailed`. All scraping/parsing lives in `api.py`, so
this file never imports requests/bs4 and never touches HTML.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, MyFuelPortalClient, MyFuelPortalError
from .const import DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MyFuelPortalCoordinator(DataUpdateCoordinator):
    """Polls the portal and exposes parsed data to the platform entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        username: str,
        password: str,
        scan_interval_hours: int = DEFAULT_SCAN_INTERVAL_HOURS,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # The source data is only ~daily, so polling hourly would just hammer
            # the portal for nothing. Interval is now caller-supplied so an
            # OptionsFlow can let the user tune it (default 12h).
            update_interval=timedelta(hours=scan_interval_hours),
        )
        self._client = MyFuelPortalClient(base_url, username, password)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest tank data; run the blocking client in the executor."""
        try:
            tanks = await self.hass.async_add_executor_job(self._client.get_tanks)
        except AuthError as err:
            # Bad credentials don't fix themselves on retry. (A future step can
            # raise ConfigEntryAuthFailed here to trigger HA's reauth flow.)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except MyFuelPortalError as err:
            # Transient scrape/HTTP issues — coordinator will retry next interval.
            raise UpdateFailed(str(err)) from err
        return {"tanks": tanks}
