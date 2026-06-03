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
from homeassistant.util import dt as dt_util

from .api import AuthError, MyFuelPortalClient, MyFuelPortalError
from .const import DEFAULT_SCAN_INTERVAL_HOURS, DELIVERY_LOOKBACK_DAYS, DOMAIN

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
        """Fetch tank data (required) and delivery history (optional).

        Tank data is the core of the integration: if it (or auth) fails, the whole
        update fails. Delivery history is best-effort — a missing/changed page is
        logged and skipped so it can never take the tank sensors down with it.
        """
        # --- Required: tanks --------------------------------------------------
        try:
            tanks = await self.hass.async_add_executor_job(self._client.get_tanks)
        except AuthError as err:
            # Bad credentials don't fix themselves on retry. (A future step can
            # raise ConfigEntryAuthFailed here to trigger HA's reauth flow.)
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except MyFuelPortalError as err:
            # Transient scrape/HTTP issues — coordinator will retry next interval.
            raise UpdateFailed(str(err)) from err

        # --- Optional: delivery history --------------------------------------
        # Query a wide window (seasonal deliveries are sparse) so the most recent
        # delivery still shows. Any failure here is logged, not fatal.
        now = dt_util.now()
        date_from = (now - timedelta(days=DELIVERY_LOOKBACK_DAYS)).strftime("%m/%d/%Y")
        date_to = now.strftime("%m/%d/%Y")
        deliveries: list[Any] = []
        deliveries_available = False
        try:
            deliveries = await self.hass.async_add_executor_job(
                self._client.get_deliveries, date_from, date_to
            )
            deliveries_available = True
        except MyFuelPortalError as err:
            _LOGGER.warning(
                "Delivery history unavailable (%s); continuing without it.", err
            )

        return {
            "tanks": tanks,
            "deliveries": deliveries,
            "deliveries_available": deliveries_available,
        }
