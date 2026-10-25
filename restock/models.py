"""Data types shared across the app."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"          # for a category: it is live and shows products
    OUT_OF_STOCK = "out_of_stock"  # for a category: offline (redirects home / 404)
    # We could not tell (network error, page layout changed, blocked).
    # UNKNOWN is never saved over a known state and never triggers an alert.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProductSnapshot:
    """A snapshot is a record of what I saw at one moment."""

    product_id: str
    name: str
    url: str
    status: StockStatus
    price: Optional[str] = None
    kind: str = "product"  # "product" or "category"


class Event(str, Enum):
    RESTOCK = "Back in stock"
    NEW_PRODUCT = "New product"
    CATEGORY_LIVE = "Category went live"


@dataclass(frozen=True)
class Alert:
    event: Event
    item: ProductSnapshot
