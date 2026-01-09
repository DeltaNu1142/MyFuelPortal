MyFuelPortal Home Assistant Integration

Integrate your MyFuelPortal account with Home Assistant to monitor propane tank levels, usage, and delivery information. This integration also provides a daily propane usage sensor that can be added to Home Assistant’s Energy dashboard.

THIS INTEGRATION NEEDS TO BE EDITED TO ADD THE SPECIFIC GAS PROVIDER'S URL AS CONFIGURABLE. CURRENTLY ALL REFERENCES TO THE GAS PROVIDER URL ARE CALLED OUT AS "MYPROVIDER".

Features

Monitors your propane tank(s) individually

Tracks:

Current gallons

Tank percentage

Tank capacity

Last delivery date

Last reading date

Calculates daily propane usage for each tank

Fully compatible with Home Assistant Energy dashboard for propane usage tracking

Unique IDs for all sensors to allow management in Home Assistant UI

Auto-refreshes tank data every 12 hours

Installation

Place the myfuelportal folder in your config/custom_components/ directory:

config/
└── custom_components/
    └── myfuelportal/


Ensure the folder contains:

__init__.py

config_flow.py

manifest.json

sensor.py

strings.json

Restart Home Assistant.

Configuration

Go to Settings → Devices & Services → Add Integration.

Search for MyFuelPortal and select it.

Enter your Email Address and Password for MyFuelPortal.

After setup, sensors for your tank(s) will be automatically added.

Optionally, add the Daily Usage sensor to the Energy dashboard under Gas.

Sensor List
Sensor	Description
<tank_name> gallons	Current gallons in the tank
<tank_name> percent	Tank fill level (%)
<tank_name> capacity	Tank capacity (gallons)
<tank_name> last_delivery	Last delivery date (ISO format)
<tank_name> reading_date	Last reading date (ISO format)
<tank_name> Daily Usage	Estimated gallons used per day (for Energy dashboard)

All sensors have unique IDs for easy UI management.

Notes

Currently supports single or multiple tanks.

Daily usage is calculated based on previous reading versus current gallons.

Tanks are refreshed every 12 hours.

Login uses the official MyFuelPortal login page: https://MYPROVIDER.myfuelportal.com/Account/Login.

Requirements

Home Assistant 2023.7 or later

Python packages:

requests

beautifulsoup4

Troubleshooting

Invalid credentials: Double-check your MyFuelPortal email and password.

Cannot connect: Ensure your HA instance can access https://MYPROVIDER.myfuelportal.com/.

No sensors created: Make sure your MyFuelPortal account has active tank data.

Energy dashboard not updating: Wait until the next scheduled refresh (every 12 hours) or force refresh in HA.

Changelog

1.0.4

Added daily usage sensor for Energy dashboard

Updated tank dates to ISO format

Fixed unique IDs for all sensors

1.0.3

Initial functional version with tanks and readings

Author

Custom integration by DeltaNu1142 and ChatGPT.
Inspired by MyFuelPortal customer portal.
