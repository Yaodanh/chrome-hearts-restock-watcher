"""Watch a whole store for pop-up drops, new products and restocks.

Chrome Hearts sells clothing online only during short pop-ups. Between drops
there is no product page to watch, so instead this source watches the store
itself:

1. Category pages. Clothing categories such as /hoodies exist all year but are
   switched off: requesting them redirects to the homepage. When a pop-up opens,
   the page starts listing products. Unknown paths return 404 instead.
2. New categories, found in the sitemap and the homepage menu, are added
   automatically.
3. Every product on a live category page is checked on its own page
   (schema.org availability), so new items and restocks are both caught.

Each category and each product becomes one ProductSnapshot; detector.py then
decides what deserves an email.
"""
import logging
import re
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http import PoliteClient
from .models import ProductSnapshot, StockStatus
from .sources import iter_json_ld, parse_product_page, read_schema_name

log = logging.getLogger(__name__)

# /socks/ch-logo-socks/176354XXXXXX349.html
PRODUCT_PATH = re.compile(r"^/[a-z0-9-]+/[a-z0-9-]+/([A-Za-z0-9]+)\.html$")
# /hoodies  (one path segment, no file extension)
CATEGORY_PATH = re.compile(r"^/[a-z0-9-]+$")
NOT_CATEGORIES = {"/login", "/cart", "/checkout", "/contact", "/account", "/wishlist", "/search"}


def category_status(requested_path: str, final_url: str, status_code: int,
                    product_urls: List[str]) -> StockStatus:
    """Is a category page live?

    live    -> 200, not bounced to the homepage, lists at least one product
    offline -> bounced to the homepage, or 404
    unknown -> anything else (server error, blocked, empty page)
    """
    final_path = urlparse(final_url).path or "/"
    if status_code == 404 or (status_code == 200 and final_path == "/" and requested_path != "/"):
        return StockStatus.OUT_OF_STOCK
    if status_code == 200 and product_urls:
        return StockStatus.IN_STOCK
    return StockStatus.UNKNOWN


def product_urls_on_page(html: str, base_url: str) -> List[str]:
    """Product links on a category page.

    Prefers the schema.org ItemList the site embeds; falls back to <a href> links.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for item in iter_json_ld(soup):
        if item.get("@type") == "ItemList":
            for el in item.get("itemListElement") or []:
                if isinstance(el, dict) and el.get("url"):
                    found.append(urljoin(base_url, el["url"]))
    if not found:
        for a in soup.find_all("a", href=True):
            url = urljoin(base_url, a["href"])
            if PRODUCT_PATH.match(urlparse(url).path):
                found.append(url)
    return list(dict.fromkeys(found))  # de-duplicate, keep order


def category_paths_in(html_or_xml: str, base_url: str) -> Set[str]:
    """Single-segment same-site paths (likely categories) in a sitemap or page."""
    host = urlparse(base_url).netloc
    paths = set()
    for raw in re.findall(r'(?:<loc>|href=")([^<"]+)', html_or_xml):
        url = urlparse(urljoin(base_url, raw.strip()))
        if url.netloc == host and CATEGORY_PATH.match(url.path) and url.path not in NOT_CATEGORIES:
            paths.add(url.path)
    return paths


class CatalogSource:
    def __init__(self, base_url: str, categories: Iterable[str], client: PoliteClient,
                 discover: bool = True, check_products: bool = True):
        self.base_url = base_url.rstrip("/") + "/"
        self.categories = list(dict.fromkeys(categories))
        self.client = client
        self.discover = discover
        self.check_products = check_products

    def _discover_categories(self) -> List[str]:
        found = set()
        for page in ("sitemap_0.xml", ""):
            resp = self.client.get(urljoin(self.base_url, page))
            if resp is not None and resp.status_code == 200:
                found |= category_paths_in(resp.text, self.base_url)
        new = sorted(found - set(self.categories))
        if new:
            log.info("Discovered categories: %s", ", ".join(new))
        return new

    def fetch(self) -> List[ProductSnapshot]:
        paths = self.categories + (self._discover_categories() if self.discover else [])
        snapshots: List[ProductSnapshot] = []
        product_urls: Dict[str, str] = {}  # product id -> url

        for path in paths:
            url = urljoin(self.base_url, path.lstrip("/"))
            resp = self.client.get(url)
            urls: List[str] = []
            status = StockStatus.UNKNOWN
            if resp is not None:
                if resp.status_code == 200:
                    urls = product_urls_on_page(resp.text, url)
                status = category_status(path, resp.url, resp.status_code, urls)
            snapshots.append(ProductSnapshot(
                product_id=f"category:{path}", name=f"Category {path}", url=url,
                status=status, kind="category",
            ))
            if status is StockStatus.IN_STOCK:
                for purl in urls:
                    m = PRODUCT_PATH.match(urlparse(purl).path)
                    if m:
                        product_urls.setdefault(m.group(1), purl)

        for pid, purl in product_urls.items():
            snapshots.append(self._check_product(pid, purl))
        return snapshots

    def _check_product(self, pid: str, url: str) -> ProductSnapshot:
        # Name from the URL slug, in case the page can't be read.
        slug_name = urlparse(url).path.split("/")[2].replace("-", " ").upper()
        if not self.check_products:
            # Being listed on a live category page is taken as "available".
            return ProductSnapshot(pid, slug_name, url, StockStatus.IN_STOCK)
        resp = self.client.get(url)
        if resp is None or resp.status_code != 200:
            return ProductSnapshot(pid, slug_name, url, StockStatus.UNKNOWN)
        status, price = parse_product_page(resp.text, {}, [])
        name = read_schema_name(BeautifulSoup(resp.text, "html.parser")) or slug_name
        return ProductSnapshot(pid, name, url, status, price)
