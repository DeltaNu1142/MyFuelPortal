"""Config + options flow for MyFuelPortal.

Two flows live here:
  * MyFuelPortalConfigFlow  — the initial "Add integration" wizard: collect the
    provider + credentials and validate them by actually logging in (via the
    shared client, so there is no duplicate login routine anymore).
  * MyFuelPortalOptionsFlow — a post-setup "Configure" dialog that lets the user
    change the poll interval without removing/re-adding the integration.
"""
from __future__ import annotations

import logging
import re

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .api import AuthError, MyFuelPortalClient, MyFuelPortalError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Bare subdomains hang off this default platform host.
_DEFAULT_DOMAIN = "myfuelportal.com"


def _to_base_url(raw: str) -> str:
    """Turn whatever the user typed into a full https origin.

    Three accepted shapes, all normalised to a base URL:
      * bare subdomain    'myprovider'                  -> https://myprovider.myfuelportal.com
      * myfuelportal host 'myprovider.myfuelportal.com' -> https://myprovider.myfuelportal.com
      * vanity domain/URL 'https://fuel.example.com/x'
                                                        -> https://fuel.example.com

    Raises ValueError on empty/garbage input so the flow can show a friendly error.
    """
    value = raw.strip()
    # Strip any scheme and path, keeping just the host.
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE)
    host = value.strip("/").split("/")[0].lower()
    if not host:
        raise ValueError("empty provider/URL")
    # No dot → treat it as a bare subdomain on the default platform domain.
    if "." not in host:
        host = f"{host}.{_DEFAULT_DOMAIN}"
    return f"https://{host}"


class MyFuelPortalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup wizard."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                base_url = _to_base_url(user_input[CONF_PROVIDER])
            except ValueError:
                errors["base"] = "invalid_provider"
            else:
                client = MyFuelPortalClient(
                    base_url,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
                try:
                    # Validate by logging in through the SAME client the
                    # coordinator will use — blocking, so run it in the executor.
                    await self.hass.async_add_executor_job(client.test_credentials)
                except AuthError:
                    errors["base"] = "invalid_auth"
                except MyFuelPortalError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001 — surface anything unexpected
                    _LOGGER.exception("Unexpected error validating MyFuelPortal login")
                    errors["base"] = "cannot_connect"
                else:
                    # One entry per portal+username so the same account can't be
                    # added twice (a different account still can).
                    await self.async_set_unique_id(
                        f"{base_url}:{user_input[CONF_USERNAME].lower()}"
                    )
                    self._abort_if_unique_id_configured()
                    host = base_url.removeprefix("https://")
                    return self.async_create_entry(
                        title=f"MyFuelPortal ({host})",
                        data={
                            CONF_BASE_URL: base_url,
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROVIDER): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Tell HA this integration has a 'Configure' (options) dialog."""
        return MyFuelPortalOptionsFlow(config_entry)


class MyFuelPortalOptionsFlow(config_entries.OptionsFlow):
    """Post-setup options: tune the poll interval (1–48 h)."""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Saving options triggers the update listener in __init__.py, which
            # reloads the entry so the new interval takes effect immediately.
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(
            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL_HOURS, default=current
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=48)),
                }
            ),
        )
