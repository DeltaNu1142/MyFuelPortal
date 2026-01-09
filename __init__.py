"""MyFuelPortal integration."""
import logging
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from bs4 import BeautifulSoup
import requests

DOMAIN = "myfuelportal"
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config):
    """Set up the integration (no YAML config)."""
    return True

async def async_setup_entry(hass, entry):
    """Set up sensors from a config entry."""
    username = entry.data["username"]
    password = entry.data["password"]

    coordinator = MyFuelPortalDataCoordinator(hass, username, password)
    await coordinator.async_config_entry_first_refresh()

    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True

async def async_unload_entry(hass, entry):
    """Unload sensors."""
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])

class MyFuelPortalDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch MyFuelPortal data."""

    def __init__(self, hass, username, password):
        super().__init__(
            hass,
            _LOGGER,
            name="MyFuelPortal",
            update_interval=timedelta(hours=12),
        )
        self.username = username
        self.password = password

    async def _async_update_data(self):
        """Fetch data from the portal."""

        def fetch_data():
            LOGIN_URL = "https://MYPROVIDER.myfuelportal.com/Account/Login?ReturnUrl=%2F"
            DATA_URL = "https://MYPROVIDER.myfuelportal.com/Tank"
            session = requests.Session()

            # Get login page for CSRF token
            login_page = session.get(LOGIN_URL, timeout=10)
            login_page.raise_for_status()
            soup = BeautifulSoup(login_page.text, "html.parser")
            token_input = soup.find("input", {"name": "__RequestVerificationToken"})
            if not token_input:
                raise UpdateFailed("Cannot fetch CSRF token")
            token = token_input["value"]

            payload = {
                "EmailAddress": self.username,
                "Password": self.password,
                "RememberMe": "false",
                "__RequestVerificationToken": token
            }
            resp = session.post(LOGIN_URL, data=payload, timeout=10)
            if "/Account/Login" in resp.url:
                raise UpdateFailed("Login failed: invalid credentials")

            resp = session.get(DATA_URL, timeout=10)
            if resp.status_code != 200:
                raise UpdateFailed("Failed to fetch tank page")

            soup = BeautifulSoup(resp.text, "html.parser")
            tank_divs = soup.select("div.tank-row")
            tanks = []

            for div in tank_divs:
                try:
                    name_tag = div.select_one(".text-larger")
                    name = name_tag.get_text(strip=True) if name_tag else "Tank"

                    percent_tag = div.select_one(".progress-bar")
                    percent = float(percent_tag.get_text(strip=True).replace("%", "")) if percent_tag else None

                    gallons_tag = div.find(text=lambda t: t and "Approximately" in t)
                    gallons = float(gallons_tag.split()[1]) if gallons_tag else None

                    reading_tag = div.find(text=lambda t: t and "Reading Date:" in t)
                    reading_date = None
                    if reading_tag:
                        reading_date = reading_tag.replace("Reading Date:", "").strip()

                    delivery_tag = div.find(text=lambda t: t and "Last Delivery:" in t)
                    last_delivery = None
                    if delivery_tag:
                        last_delivery = delivery_tag.replace("Last Delivery:", "").strip()

                    capacity = round(gallons / (percent / 100), 1) if gallons and percent else None

                    tanks.append({
                        "name": name,
                        "tank_level": percent,
                        "gallons": gallons,
                        "capacity": capacity,
                        "reading_date": reading_date,
                        "last_delivery": last_delivery,
                        "prev_gallons": gallons,
                        "prev_date": reading_date,
                        "daily_usage": 0,
                        "cumulative_usage": 0
                    })
                except Exception as e:
                    _LOGGER.warning("Failed to parse a tank: %s", e)

            return {"tanks": tanks}

        return await self.hass.async_add_executor_job(fetch_data)
