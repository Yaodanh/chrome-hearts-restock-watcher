"""Where stock information comes from.

Every source implements the same tiny interface: ``fetch() -> list[ProductSnapshot]``.
The rest of the app never knows whether data came from a JSON file or a live
website, so the website part can break (layout change, blocking) without
touching detection, storage, or email code.
"""
import json
import logging
from pathlib import Path
from typing import Iterable, List, Optional, Protocol, Tuple

from bs4 import BeautifulSoup

from .http import PoliteClient
from .models import ProductSnapshot, StockStatus

log = logging.getLogger(__name__)


class StockSource(Protocol):
    def fetch(self) -> List[ProductSnapshot]:
        ...


class MockSource:
    """Reads products from a local JSON file.

    Used for demos and tests: edit the file, flip a product to in_stock,
    and run again to see an alert without touching any real website.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def fetch(self) -> List[ProductSnapshot]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [
            ProductSnapshot(
                product_id=item["id"],
                name=item["name"],
                url=item.get("url", ""),
                status=StockStatus(item["status"]),
                price=item.get("price"),
            )
            for item in raw["products"]
        ]


def parse_stock_status(html: str, out_of_stock_texts: Iterable[str],
                       in_stock_selector: str = "") -> StockStatus:
    """Fallback for sites without structured data: guess from page text.

    Rules, in order:
    1. If any "sold out" phrase appears in the visible text -> OUT_OF_STOCK.
    2. Else if an in-stock selector is configured and matches -> IN_STOCK.
    3. Else if a selector is configured but missing -> UNKNOWN
       (the page probably changed; better silent than a false alert).
    4. No selector configured and no sold-out phrase -> IN_STOCK.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True).lower()

    for phrase in out_of_stock_texts:
        if phrase.lower() in text:
            return StockStatus.OUT_OF_STOCK

    if in_stock_selector:
        return StockStatus.IN_STOCK if soup.select_one(in_stock_selector) else StockStatus.UNKNOWN

    return StockStatus.IN_STOCK


# schema.org availability values -> our status
_SCHEMA_IN = {"instock", "limitedavailability", "onlineonly", "instoreonly"}
_SCHEMA_OUT = {"outofstock", "soldout", "discontinued"}


def read_schema_org(soup: BeautifulSoup) -> Tuple[StockStatus, Optional[str]]:
    """Read the schema.org Product block many shops embed for search engines.

    Example (chromehearts.com):
      {"@type":"Product", ..., "offers":{"price":"255.00","availability":"http://schema.org/InStock"}}
    Returns (UNKNOWN, None) if there is no usable block.
    """
    for item in iter_json_ld(soup):
        if item.get("@type") != "Product":
            continue
        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        value = str(offers.get("availability", "")).rsplit("/", 1)[-1].lower()
        price = offers.get("price")
        price = f"${price}" if price else None
        if value in _SCHEMA_IN:
            return StockStatus.IN_STOCK, price
        if value in _SCHEMA_OUT:
            return StockStatus.OUT_OF_STOCK, price
        return StockStatus.UNKNOWN, price
    return StockStatus.UNKNOWN, None


def iter_json_ld(soup: BeautifulSoup):
    """Yield every JSON-LD object on the page."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                yield item


def read_schema_name(soup: BeautifulSoup) -> Optional[str]:
    for item in iter_json_ld(soup):
        if item.get("@type") == "Product" and item.get("name"):
            return str(item["name"]).strip()
    return None


def read_variant_swatch(soup: BeautifulSoup, attribute: str, value: str) -> StockStatus:
    """Stock for one color or size, from Salesforce Commerce Cloud swatch buttons.

    On chromehearts.com each option looks like:
      <div data-attr="color"> ... <span data-attr-value="WHT" class="color-value ... unselectable">
    "selectable" = can be bought, "unselectable" = sold out.
    """
    container = soup.find(attrs={"data-attr": attribute})
    if container is None:
        return StockStatus.UNKNOWN
    swatch = container.find(attrs={"data-attr-value": value})
    if swatch is None:
        return StockStatus.UNKNOWN
    classes = set(swatch.get("class") or [])
    if "unselectable" in classes:
        return StockStatus.OUT_OF_STOCK
    if "selectable" in classes:
        return StockStatus.IN_STOCK
    return StockStatus.UNKNOWN


def parse_product_page(html: str, product: dict,
                       out_of_stock_texts: Iterable[str]) -> Tuple[StockStatus, Optional[str]]:
    """Pick the most reliable signal available for this product.

    1. A specific variant was requested (e.g. color WHT) -> its swatch.
    2. Otherwise the page's schema.org availability.
    3. Otherwise fall back to guessing from text.
    """
    soup = BeautifulSoup(html, "html.parser")
    schema_status, price = read_schema_org(soup)

    variant = product.get("variant")
    if variant:
        return read_variant_swatch(soup, variant["attribute"], variant["value"]), price

    if schema_status is not StockStatus.UNKNOWN:
        return schema_status, price

    return parse_stock_status(html, out_of_stock_texts, product.get("in_stock_selector", "")), price


class WebSource:
    """Checks a fixed list of product pages (from config.yaml).

    Any failure for one product becomes UNKNOWN for that product only.
    """

    def __init__(self, products: list, out_of_stock_texts: list,
                 user_agent: str, delay_seconds: float = 3.0,
                 timeout_seconds: float = 15.0, session=None):
        self.products = products
        self.out_of_stock_texts = out_of_stock_texts
        self.client = PoliteClient(user_agent, delay_seconds, timeout_seconds, session)
        self.session = self.client.session

    def fetch(self) -> List[ProductSnapshot]:
        return [self._check_one(p) for p in self.products]

    def _check_one(self, product: dict) -> ProductSnapshot:
        url = product["url"]
        status, price = StockStatus.UNKNOWN, None
        resp = self.client.get(url)
        if resp is not None and resp.status_code == 200:
            status, price = parse_product_page(resp.text, product, self.out_of_stock_texts)
        elif resp is not None:
            log.warning("HTTP %s for %s", resp.status_code, url)
        return ProductSnapshot(
            product_id=product["id"], name=product["name"], url=url, status=status, price=price
        )
