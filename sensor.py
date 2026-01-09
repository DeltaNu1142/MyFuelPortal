import logging
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import requests

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

_LOGGER = logging.getLogger(__name__)

def to_iso(date_str):
    """Convert tank portal's date ('MM/DD/YYYY') to ISO 'YYYY-MM-DD'."""
    if not date_str or date_str.strip() == "":
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
        return dt.date().isoformat()
    except Exception:
        return date_str  # fallback: keep original


async def async_setup_entry(hass, entry, async_add_entities):
    username = entry.data["username"]
    password = entry.data["password"]

    coordinator = MyFuelPortalDataCoordinator(hass, username, password)
    await coordinator.async_refresh()

    sensors = []
    for tank in coordinator.data.get("tanks", []):
        tank_name = tank["name"]
        sensors.extend([
            MyFuelPortalSensor(coordinator, tank_name, "gallons"),
            MyFuelPortalSensor(coordinator, tank_name, "percent"),
            MyFuelPortalSensor(coordinator, tank_name, "capacity"),
            MyFuelPortalSensor(coordinator, tank_name, "last_delivery"),
            MyFuelPortalSensor(coordinator, tank_name, "reading_date"),
            MyFuelPortalUsageSensor(coordinator, tank_name),
        ])
    async_add_entities(sensors, True)


class MyFuelPortalDataCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch MyFuelPortal tank data."""

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
                    if not name_tag:
                        continue
                    name = name_tag.get_text(strip=True)

                    percent_tag = div.select_one(".progress-bar")
                    percent = float(percent_tag.get_text(strip=True).replace("%", ""))

                    gallons_tag = div.find(text=lambda t: t and "Approximately" in t)
                    gallons = float(gallons_tag.split()[1]) if gallons_tag else None

                    reading_tag = div.find(text=lambda t: t and "Reading Date:" in t)
                    reading_date = None
                    if reading_tag:
                        reading_date = to_iso(reading_tag.replace("Reading Date:", "").strip())

                    delivery_tag = div.find(text=lambda t: t and "Last Delivery:" in t)
                    last_delivery = None
                    if delivery_tag:
                        last_delivery = to_iso(delivery_tag.replace("Last Delivery:", "").strip())

                    capacity = round(gallons / (percent / 100), 1) if gallons and percent else None

                    tanks.append({
                        "name": name,
                        "percent": percent,
                        "gallons": gallons,
                        "capacity": capacity,
                        "last_delivery": last_delivery,
                        "reading_date": reading_date,
                        "prev_gallons": gallons,
                        "prev_date": reading_date
                    })
                except Exception as e:
                    _LOGGER.warning("Failed to parse a tank: %s", e)

            return {"tanks": tanks}

        return await self.hass.async_add_executor_job(fetch_data)


class MyFuelPortalSensor(Entity):
    """Generic sensor for a tank and value."""

    def __init__(self, coordinator, tank_name, sensor_type):
        self.coordinator = coordinator
        self.tank_name = tank_name
        self.sensor_type = sensor_type

        base_slug = re.sub(r"[^a-z0-9_]+", "", tank_name.lower().replace(" ", "_"))
        self._slug = f"{base_slug}_{sensor_type}"

    @property
    def name(self):
        return f"{self.tank_name} {self.sensor_type.capitalize()}"

    @property
    def unique_id(self):
        return f"myfuelportal_{self._slug}"

    @property
    def state(self):
        for tank in self.coordinator.data.get("tanks", []):
            if tank["name"] == self.tank_name:
                return tank.get(self.sensor_type)
        return None

    @property
    def device_class(self):
        if self.sensor_type in ["gallons", "capacity"]:
            return SensorDeviceClass.GAS
        return None

    @property
    def state_class(self):
        if self.sensor_type in ["gallons", "capacity"]:
            return SensorStateClass.TOTAL_INCREASING
        return None

    async def async_update(self):
        await self.coordinator.async_request_refresh()


class MyFuelPortalUsageSensor(Entity):
    """Sensor to track daily gallons used for a tank (for Energy dashboard)."""

    def __init__(self, coordinator, tank_name):
        self.coordinator = coordinator
        self.tank_name = tank_name
        self._slug = re.sub(r"[^a-z0-9_]+", "", tank_name.lower().replace(" ", "_")) + "_usage"
        self._prev_gallons = None
        self._prev_date = None

    @property
    def name(self):
        return f"{self.tank_name} Daily Usage"

    @property
    def unique_id(self):
        return f"myfuelportal_{self._slug}"

    @property
    def state(self):
        for tank in self.coordinator.data.get("tanks", []):
            if tank["name"] == self.tank_name:
                curr_gallons = tank.get("gallons")
                reading_date = tank.get("reading_date")

                if curr_gallons is None or reading_date is None:
                    return None

                try:
                    curr_date = datetime.fromisoformat(reading_date).date()
                    if self._prev_gallons is not None and self._prev_date is not None:
                        days = (curr_date - self._prev_date).days
                        if days > 0:
                            usage = (self._prev_gallons - curr_gallons) / days
                            return round(usage, 2)
                    # First run, store values
                    self._prev_gallons = curr_gallons
                    self._prev_date = curr_date
                except Exception:
                    return None
        return None

    @property
    def device_class(self):
        return SensorDeviceClass.GAS

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    async def async_update(self):
        await self.coordinator.async_request_refresh()
