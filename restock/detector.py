"""The core rules: what deserves an email?"""
from typing import Dict, List

from .models import Alert, Event, ProductSnapshot, StockStatus

IN, OUT = StockStatus.IN_STOCK, StockStatus.OUT_OF_STOCK


def find_alerts(previous: Dict[str, StockStatus],
                current: List[ProductSnapshot]) -> List[Alert]:
    """Compare this check with the last one.

    - Very first run (nothing remembered yet): record a baseline, no alerts.
      Otherwise you'd get an email about everything already on the site.
    - A category goes offline -> live, or appears   -> CATEGORY_LIVE (a pop-up just opened)
    - A product we've never seen appears in stock   -> NEW_PRODUCT
    - A product goes from sold out to in stock      -> RESTOCK
    - Staying in stock, or UNKNOWN, never alerts, so each event emails once.
      A new product that is already sold out is only recorded; if it comes back
      later that's a RESTOCK.
    """
    if not previous:
        return []

    alerts = []
    for item in current:
        if item.status is not IN:
            continue
        before = previous.get(item.product_id)
        if item.kind == "category":
            if before is OUT or before is None:  # switched on, or brand-new category
                alerts.append(Alert(Event.CATEGORY_LIVE, item))
        elif before is None:
            alerts.append(Alert(Event.NEW_PRODUCT, item))
        elif before is OUT:
            alerts.append(Alert(Event.RESTOCK, item))
    return alerts
