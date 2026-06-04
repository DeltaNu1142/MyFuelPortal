"""MyFuelPortal data update coordinator.

The coordinator's only jobs are: hold the client, run it on a schedule (off the
event loop, since `requests` is blocking), and translate the client's exceptions
into Home Assistant's `UpdateFailed`. All scraping/parsing lives in `api.py`, so
this file never imports requests/bs4 and never touches HTML.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
        # Manual $/gal fallback — owned/persisted by the number entity (number.py)
        # and read by the effective-price sensor. None until the number restores.
        self.manual_price_per_gallon: float | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch tank data (required) plus account info and delivery history
        (both optional/best-effort).

        Tank data is the core: if it (or auth) fails, the whole update fails. The
        account page and delivery history are best-effort — a missing/changed page
        is logged and skipped so it never takes the tank sensors down. The
        account's "Customer Since" date, when present, is the exact start of the
        delivery query (otherwise a generous lookback).
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

        # --- Optional: account (home page) -----------------------------------
        account: dict[str, Any] | None = None
        try:
            account = await self.hass.async_add_executor_job(self._client.get_account)
        except MyFuelPortalError as err:
            _LOGGER.warning("Account info unavailable (%s); continuing without it.", err)

        # --- Optional: delivery history --------------------------------------
        # Start from the account's exact "Customer Since" when we have it; else a
        # generous lookback (seasonal deliveries are sparse). The portal returns
        # the whole range in one response, so a wide start just gives full history.
        now = dt_util.now()
        date_to = now.strftime("%m/%d/%Y")
        date_from = (now - timedelta(days=DELIVERY_LOOKBACK_DAYS)).strftime("%m/%d/%Y")
        if account and account.get("customer_since"):
            try:
                date_from = datetime.fromisoformat(
                    account["customer_since"]
                ).strftime("%m/%d/%Y")
            except (ValueError, TypeError):
                pass

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

        # NOTE: this logs in 3× per poll (tanks/account/deliveries each open a
        # fresh session). Fine at a ~12h cadence; a single-session fetch_all is a
        # future optimization.
        return {
            "tanks": tanks,
            "deliveries": deliveries,
            "deliveries_available": deliveries_available,
            "account": account,
            "account_available": account is not None,
        }
