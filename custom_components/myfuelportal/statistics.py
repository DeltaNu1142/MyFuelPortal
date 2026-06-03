"""Backfill MyFuelPortal delivery history into long-term statistics.

The delivery sensors only record forward from install, so they can't show your
*past* deliveries (spend/price over the years). This pushes the scraped delivery
history into Home Assistant's long-term statistics as EXTERNAL statistics
(statistic_id `myfuelportal:...`), giving real historical graphs (Statistics
cards / the History panel) back to the account's first delivery.

Three series per tank:
  * `<tank>_delivered_spend`   cumulative $ spent on deliveries (has_sum)
  * `<tank>_delivered_gallons` cumulative gallons delivered     (has_sum)
  * `<tank>_delivered_price`   $/gal paid per delivery           (has_mean)

Re-importing is idempotent: the recorder keys statistics by their start time, so
re-running each poll just refreshes the same points (and picks up new deliveries).
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify

from .api import DeliveryData
from .const import DOMAIN


def _stat_start(date_iso: str) -> datetime | None:
    """A delivery's ISO date -> an hour-aligned UTC datetime.

    Statistics start times must be hour-aligned; deliveries are date-granular, so
    we anchor each at midnight UTC of its date.
    """
    try:
        parsed = datetime.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


@callback
def async_import_delivery_statistics(
    hass: HomeAssistant, tank_name: str, deliveries: list[DeliveryData]
) -> None:
    """Import one tank's delivery history as external statistics."""
    rows = sorted(
        (d for d in deliveries if d.get("date")),
        key=lambda d: d["date"] or "",
    )
    if not rows:
        return

    prefix = f"{DOMAIN}:{slugify(tank_name)}"
    spend: list[StatisticData] = []
    gallons: list[StatisticData] = []
    price: list[StatisticData] = []
    spend_sum = 0.0
    gallons_sum = 0.0

    for delivery in rows:
        start = _stat_start(delivery["date"] or "")
        if start is None:
            continue
        cost = delivery.get("cost") or 0.0
        delivered = delivery.get("gallons") or 0.0
        per_gallon = delivery.get("price_per_gallon")

        spend_sum += cost
        gallons_sum += delivered
        spend.append(StatisticData(start=start, state=cost, sum=spend_sum))
        gallons.append(StatisticData(start=start, state=delivered, sum=gallons_sum))
        if per_gallon is not None:
            price.append(
                StatisticData(start=start, mean=per_gallon, min=per_gallon, max=per_gallon)
            )

    async_add_external_statistics(
        hass,
        StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"{tank_name} Delivered Spend",
            source=DOMAIN,
            statistic_id=f"{prefix}_delivered_spend",
            unit_of_measurement="USD",
        ),
        spend,
    )
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"{tank_name} Delivered Gallons",
            source=DOMAIN,
            statistic_id=f"{prefix}_delivered_gallons",
            unit_of_measurement="gal",
        ),
        gallons,
    )
    if price:
        async_add_external_statistics(
            hass,
            StatisticMetaData(
                has_mean=True,
                has_sum=False,
                name=f"{tank_name} Delivered Price",
                source=DOMAIN,
                statistic_id=f"{prefix}_delivered_price",
                unit_of_measurement="USD/gal",
            ),
            price,
        )
