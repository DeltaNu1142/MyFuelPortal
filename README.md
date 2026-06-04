# MyFuelPortal — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Monitor your propane (or other fuel) tank in Home Assistant via [MyFuelPortal](https://www.myfuelportal.com/) — tank level, deliveries, **per-gallon pricing**, total spend, and account info. Many fuel providers use MyFuelPortal (some on their own vanity domain) for online tank monitoring; this integration logs into your provider's portal, scrapes it, and creates sensors for each tank.

## Features

- **Any MyFuelPortal provider** — enter the subdomain *or* a full vanity URL (for providers on a custom domain)
- **Tank monitoring** — gallons, level %, capacity, last delivery, daily usage
- **Delivery history & pricing** — last delivery cost/gallons, **derived `$/gal` and `$/ft³`**, total spend, total delivered
- **Account info** — customer-since date, balance, status
- **Effective price** — follows your actual delivered price, with a **manual override** for providers that don't publish cost
- **Energy-dashboard ready** — a Cumulative Usage sensor (ft³, `total_increasing`) for gas consumption *and* a `$/ft³` price entity for cost
- **Configurable poll interval** (1–48 h)
- **Resilient** — the delivery/account pages are optional; if one changes or is missing, your tank sensors keep working
- **State restoration** — cumulative and daily usage survive restarts

## Entities

### Tank — one device per tank
| Entity | Description |
|---|---|
| Gallons | Current gallons in tank |
| Level | Tank fill (%) |
| Capacity | Tank capacity (gallons) |
| Last Delivery | Date of last delivery |
| Reading Date | Date of last monitor reading |
| Daily Usage | Estimated gallons/day (survives restarts) |
| Cumulative Usage | Total consumed, **ft³** — for the Energy gas dashboard |
| Effective Price per Gallon | Latest delivered `$/gal`, else the manual override |

### Delivery — added when delivery history is available
| Entity | Description |
|---|---|
| Last Delivery Cost | Cost of the most recent delivery |
| Last Delivery Gallons | Gallons of the most recent delivery |
| Price per Gallon | Derived `$/gal` (cost ÷ gallons) |
| Price per Cubic Foot | `$/ft³` — use as the Energy gas "current price" |
| Total Spend | Lifetime delivery spend |
| Total Delivered Gallons | Lifetime gallons delivered |
| Average Daily Usage | Avg gal/day over the latest delivery cycle (from history) |

### Account — one device per account
| Entity | Description |
|---|---|
| Customer Since | Account start date |
| Account Balance | Current balance |
| Account Status | e.g. *Active* |
| Manual Price per Gallon | Adjustable `$/gal` fallback (a `number`) |

## Installation

### HACS (recommended)

1. **HACS** → **Integrations** → **⋮** (top right) → **Custom repositories**
2. Add this repository URL, category **Integration**:
   ```
   https://github.com/DeltaNu1142/MyFuelPortal
   ```
3. Click **Add**, find **MyFuelPortal**, **Install**, then **restart Home Assistant**

### Manual

1. Copy `custom_components/myfuelportal/` into your `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. **Settings** → **Devices & Services** → **Add Integration** → **MyFuelPortal**
2. Enter:
   - **Provider** — your portal's **subdomain** (e.g. `myprovider` for `myprovider.myfuelportal.com`) **or** the full URL if your provider uses a custom domain (e.g. `https://fuel.example.com`)
   - **Email** and **Password** for your portal login
3. Tank, delivery, and account entities are created automatically.

**Options:** open the integration's **Configure** dialog to set the **poll interval** (default 12 h — the source data only updates about once a day).

## Energy Dashboard

1. **Settings** → **Dashboards** → **Energy** → **Add gas source**
2. **Gas consumption** → the tank's **Cumulative Usage** sensor (ft³)
3. **Cost** → *Use an entity with the current price* → the tank's **Price per Cubic Foot** (your real delivered price) — or set a static price

## How It Works

After logging in (the portal's standard ASP.NET form + anti-forgery token), the integration scrapes:

- `/Tank` — tank readings **(required)**
- `/Delivery/History` — delivery history, via a filtered POST *(optional)*
- `/` — account info *(optional)*

Tank data is required; the delivery and account pages are best-effort, so a change to one of them won't take the rest down. Accounts with **multiple tanks** are supported. The satellite monitor typically updates readings about once per day.

### A note on usage data

There are two kinds of usage, and they behave differently:

- **Daily Usage** and **Cumulative Usage** are measured from the tank monitor's level reading. The monitor only posts a new reading **about once a day**, and these only register when the level actually **drops** — so they start empty after setup and update slowly in low-use seasons (or whenever little fuel is being drawn).
- **Average Daily Usage** is computed from your **delivery history** (gallons delivered ÷ days since the previous delivery), so it's **populated immediately** and stays steady even when the tank level is barely moving.

Forcing a refresh (calling `homeassistant.update_entity` on any entity, or reloading the integration) re-scrapes the portal on demand, but it can't make the monitor post a new level reading faster, so it won't speed up the level-based usage sensors.

## Troubleshooting

| Issue | Solution |
|---|---|
| Invalid credentials | Re-check your portal email and password |
| Cannot connect | Verify HA can reach your portal, and that the subdomain/URL is correct |
| No tank sensors | Ensure the account has active tank data |
| No delivery/price sensors | Your provider may not expose delivery history; the **Manual Price per Gallon** override still drives the effective price |
| Values look stale | The source updates ~daily — wait for the next poll, or reload the integration |

Enable debug logging to see what's being scraped:

```yaml
logger:
  logs:
    custom_components.myfuelportal: debug
```

## Requirements

- Home Assistant **2024.1** or later
- A MyFuelPortal account with your fuel provider

## Credits

Original integration by [DeltaNu1142](https://github.com/DeltaNu1142), refactored into a HACS-compatible, configurable integration by [floydpink](https://github.com/floydpink). This version builds on that work and adds delivery history & derived pricing, account info, vanity-URL support, an options flow, graceful degradation when optional pages change, and a testable scraper client (with unit tests over saved HTML fixtures).

## License

Released under the [MIT License](LICENSE), Copyright (c) 2026 DeltaNu1142.
