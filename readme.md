MyFuelPortal Integration for Home Assistant

Overview

The MyFuelPortal integration allows you to monitor propane tanks from MyFuelPortal
 directly in Home Assistant.

It provides:

Tank level (%)

Gallons remaining

Tank capacity

Last reading date (ISO)

Last delivery date (ISO)

Daily propane usage sensor for the Energy dashboard

Cumulative propane usage sensor

All sensors support unique IDs, enabling full UI management.

Features
Sensor	Unit	Description
Tank Level	%	Current fill percentage of your propane tank
Gallons Remaining	gallons	Approximate gallons left in the tank
Tank Capacity	gallons	Calculated tank capacity
Last Reading Date	date	Last date the tank was read (ISO format)
Last Delivery Date	date	Date of the last propane delivery (ISO format)
Daily Usage	gallons	Propane used per day (for Energy dashboard)
Cumulative Usage	gallons	Total propane used over time
Installation

Copy the myfuelportal folder into your Home Assistant custom_components/ directory.

Ensure the following files are included:

__init__.py

sensor.py

config_flow.py

manifest.json

strings.json

Optional: pgag.png logo

Restart Home Assistant.

Configuration
UI Configuration (Recommended)

Go to Settings → Devices & Services → Add Integration.

Search for MyFuelPortal.

Enter your email and password for MyFuelPortal.

Click Submit.

The integration will create all tank sensors automatically.

YAML Configuration

This integration does not require YAML configuration. All setup is done via the UI.

Energy Dashboard Integration

The Daily Usage sensor is compatible with Home Assistant’s Energy dashboard:

Navigate to Settings → Energy.

Add Propane Usage using the entity created:
sensor.<your_tank_name>_daily_usage

This allows you to track propane consumption trends over time.

Notes

Multiple tanks are supported. Each tank will have its own set of sensors.

The cumulative usage sensor tracks total propane used over time and updates only when the tank level decreases.

The integration fetches updates every 12 hours.

Requirements

Home Assistant 2026+

Python packages: requests, beautifulsoup4

Troubleshooting

Cannot Connect: Check your credentials and ensure the portal URL is correct (https://MYPROVIDER.myfuelportal.com).

No Entities Created: Restart Home Assistant and ensure custom_components/myfuelportal/ contains all required files.

Energy Sensor Not Updating: Wait for the next coordinator refresh or trigger an update via the UI.

Developer Info

Domain: myfuelportal

Coordinator: Single update coordinator handles all tank data

Update Interval: 12 hours

Unique IDs: Each sensor has a unique ID for proper UI integration

License

MIT License – you are free to modify and use this integration.