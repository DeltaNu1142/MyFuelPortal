import logging
import voluptuous as vol
from homeassistant import config_entries

_LOGGER = logging.getLogger(__name__)
DOMAIN = "myfuelportal"

class MyFuelPortalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MyFuelPortal."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            from bs4 import BeautifulSoup
            import requests

            def login():
                LOGIN_URL = "https://pgagnon.myfuelportal.com/Account/Login?ReturnUrl=%2F"
                session = requests.Session()
                login_page = session.get(LOGIN_URL, timeout=10)
                login_page.raise_for_status()
                soup = BeautifulSoup(login_page.text, "html.parser")
                token_input = soup.find("input", {"name": "__RequestVerificationToken"})
                if not token_input:
                    return False
                token = token_input["value"]
                payload = {
                    "EmailAddress": user_input["username"],
                    "Password": user_input["password"],
                    "RememberMe": "false",
                    "__RequestVerificationToken": token
                }
                resp = session.post(LOGIN_URL, data=payload, timeout=10)
                return "/Account/Login" not in resp.url

            try:
                success = await self.hass.async_add_executor_job(login)
            except Exception as e:
                _LOGGER.error("Connection error: %s", e)
                return self.async_abort(reason="cannot_connect")

            if success:
                return self.async_create_entry(
                    title="MyFuelPortal Account",
                    data={
                        "username": user_input["username"],
                        "password": user_input["password"]
                    }
                )
            errors["base"] = "invalid_auth"

        data_schema = vol.Schema({
            vol.Required("username"): str,
            vol.Required("password"): str
        })
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
