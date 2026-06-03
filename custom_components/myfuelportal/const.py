"""Constants for the MyFuelPortal integration."""

DOMAIN = "myfuelportal"

# Config-entry data keys (collected in the config flow, stored on the entry).
# CONF_BASE_URL is the full portal origin (supports vanity domains). CONF_PROVIDER
# is the legacy key (a bare subdomain) kept only so entries created before
# vanity-URL support still load — see `_base_url_from_entry` in __init__.py.
CONF_BASE_URL = "base_url"
CONF_PROVIDER = "provider"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Options key (set in the OptionsFlow after setup) + its default.
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"
DEFAULT_SCAN_INTERVAL_HOURS = 12

# Liquid gallons → cubic feet (geometric volume; 1 ft³ = 7.48052 US gal). The
# cumulative-usage sensor accumulates consumed gallons and converts with this so
# it carries a unit the HA Energy dashboard's Gas section accepts (ft³).
GALLONS_TO_CUBIC_FEET = 0.133681

# How far back to query delivery history. The filtered POST returns ALL matching
# rows in one response (the grid's "10 per page" is client-side display only), so
# a wide window simply yields the complete history. Defaulted generously here;
# once the account "Customer Since" date is scraped it becomes the exact start.
# ~12 years covers essentially any account on this platform.
DELIVERY_LOOKBACK_DAYS = 4380
