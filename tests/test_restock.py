"""Run with:  python -m unittest discover -s tests -v"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from restock.__main__ import SiteUnreachable, run_once
from restock.config import ConfigError, build_notifier
from restock.catalog import CatalogSource, category_paths_in, category_status, product_urls_on_page
from restock.detector import find_alerts
from restock.http import PoliteClient
from restock.models import Event, ProductSnapshot, StockStatus as S
from restock.notifier import EmailNotifier
from restock.sources import MockSource, WebSource, parse_product_page, parse_stock_status
from restock.store import StateStore


def snap(pid, status, name=None, kind="product"):
    return ProductSnapshot(product_id=pid, name=name or pid, url=f"https://x/{pid}",
                           status=status, kind=kind)


BASE = {"other": S.IN_STOCK}  # any remembered state = not the first run


def events(previous, current):
    return [(a.event, a.item.product_id) for a in find_alerts(previous, current)]


class DetectorTests(unittest.TestCase):
    def test_out_to_in_is_a_restock(self):
        self.assertEqual(events({"a": S.OUT_OF_STOCK}, [snap("a", S.IN_STOCK)]),
                         [(Event.RESTOCK, "a")])

    def test_first_run_is_baseline_only(self):
        self.assertEqual(events({}, [snap("a", S.IN_STOCK), snap("c", S.IN_STOCK, kind="category")]), [])

    def test_new_product_in_stock_alerts(self):
        self.assertEqual(events(BASE, [snap("a", S.IN_STOCK)]), [(Event.NEW_PRODUCT, "a")])

    def test_new_product_already_sold_out_is_silent(self):
        self.assertEqual(events(BASE, [snap("a", S.OUT_OF_STOCK)]), [])

    def test_category_switching_on_alerts(self):
        prev = {"category:/hoodies": S.OUT_OF_STOCK}
        cur = [snap("category:/hoodies", S.IN_STOCK, kind="category")]
        self.assertEqual(events(prev, cur), [(Event.CATEGORY_LIVE, "category:/hoodies")])

    def test_brand_new_live_category_alerts(self):
        cur = [snap("category:/pop-up", S.IN_STOCK, kind="category")]
        self.assertEqual(events(BASE, cur), [(Event.CATEGORY_LIVE, "category:/pop-up")])

    def test_still_in_stock_does_not_alert_again(self):
        self.assertEqual(events({"a": S.IN_STOCK}, [snap("a", S.IN_STOCK)]), [])

    def test_unknown_never_alerts(self):
        self.assertEqual(events({"a": S.OUT_OF_STOCK}, [snap("a", S.UNKNOWN)]), [])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "s.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_save_and_load(self):
        self.store.save("a", S.OUT_OF_STOCK)
        self.store.save("a", S.IN_STOCK)
        self.assertEqual(self.store.load(), {"a": S.IN_STOCK})

    def test_unknown_does_not_overwrite_known_state(self):
        self.store.save("a", S.OUT_OF_STOCK)
        self.store.save("a", S.UNKNOWN)
        self.assertEqual(self.store.load(), {"a": S.OUT_OF_STOCK})


class ParseTests(unittest.TestCase):
    TEXTS = ["sold out"]

    def test_sold_out_text(self):
        html = "<button disabled>SOLD OUT</button>"
        self.assertIs(parse_stock_status(html, self.TEXTS), S.OUT_OF_STOCK)

    def test_no_sold_out_text_means_in_stock(self):
        self.assertIs(parse_stock_status("<button>Add to bag</button>", self.TEXTS), S.IN_STOCK)

    def test_selector_present(self):
        html = '<button class="add-to-bag">Add</button>'
        self.assertIs(parse_stock_status(html, self.TEXTS, "button.add-to-bag"), S.IN_STOCK)

    def test_selector_missing_means_unknown(self):
        html = "<div>Page redesigned</div>"
        self.assertIs(parse_stock_status(html, self.TEXTS, "button.add-to-bag"), S.UNKNOWN)


FIXTURE = (Path(__file__).parent / "fixtures" / "sfcc_product.html").read_text()


class ChromeHeartsPageTests(unittest.TestCase):
    """Parsing the real page structure seen on chromehearts.com."""

    def test_product_level_uses_schema_org(self):
        status, price = parse_product_page(FIXTURE, {}, ["sold out"])
        self.assertIs(status, S.IN_STOCK)
        self.assertEqual(price, "$255.00")

    def test_schema_org_out_of_stock(self):
        html = FIXTURE.replace("schema.org/InStock", "schema.org/OutOfStock")
        self.assertIs(parse_product_page(html, {}, [])[0], S.OUT_OF_STOCK)

    def test_unavailable_color_is_out_of_stock(self):
        product = {"variant": {"attribute": "color", "value": "WHT"}}
        self.assertIs(parse_product_page(FIXTURE, product, [])[0], S.OUT_OF_STOCK)

    def test_available_color_is_in_stock(self):
        product = {"variant": {"attribute": "color", "value": "BLK"}}
        self.assertIs(parse_product_page(FIXTURE, product, [])[0], S.IN_STOCK)

    def test_white_restock_is_detected(self):
        html = FIXTURE.replace("swatch-value unselectable", "swatch-value selectable")
        product = {"variant": {"attribute": "color", "value": "WHT"}}
        self.assertIs(parse_product_page(html, product, [])[0], S.IN_STOCK)

    def test_unknown_variant_value_is_unknown(self):
        product = {"variant": {"attribute": "color", "value": "PNK"}}
        self.assertIs(parse_product_page(FIXTURE, product, [])[0], S.UNKNOWN)

    def test_hidden_country_message_is_not_mistaken_for_sold_out(self):
        # The page always contains a hidden "not available in your country" div.
        self.assertIs(parse_product_page(FIXTURE, {}, ["not available"])[0], S.IN_STOCK)


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class WebSourceTests(unittest.TestCase):
    def make_source(self, responses):
        session = mock.Mock()
        session.headers = {}
        session.get.side_effect = lambda url, timeout: responses[url]
        return WebSource(
            products=[{"id": "a", "name": "A", "url": "https://shop.test/p/a"}],
            out_of_stock_texts=["sold out"], user_agent="test", delay_seconds=0, session=session,
        )

    def test_reads_status_from_page(self):
        src = self.make_source({
            "https://shop.test/robots.txt": FakeResponse("User-agent: *\nAllow: /"),
            "https://shop.test/p/a": FakeResponse("<p>Sold Out</p>"),
        })
        self.assertIs(src.fetch()[0].status, S.OUT_OF_STOCK)

    def test_respects_robots_disallow(self):
        src = self.make_source({
            "https://shop.test/robots.txt": FakeResponse("User-agent: *\nDisallow: /p/"),
        })
        self.assertIs(src.fetch()[0].status, S.UNKNOWN)
        # product page must not have been requested
        requested = [c.args[0] for c in src.session.get.call_args_list]
        self.assertNotIn("https://shop.test/p/a", requested)

    def test_http_error_becomes_unknown(self):
        src = self.make_source({
            "https://shop.test/robots.txt": FakeResponse("", 404),
            "https://shop.test/p/a": FakeResponse("", 503),
        })
        self.assertIs(src.fetch()[0].status, S.UNKNOWN)


class EmailTests(unittest.TestCase):
    def test_sends_one_email_listing_products(self):
        smtp = mock.MagicMock()
        factory = mock.Mock(return_value=smtp)
        n = EmailNotifier("smtp.test", 465, "u", "p", "u@test", "me@test", smtp_factory=factory)
        n.send(find_alerts(BASE, [snap("a", S.IN_STOCK, "Cross Ring")]))
        sent = smtp.__enter__.return_value.send_message.call_args.args[0]
        self.assertIn("Cross Ring", sent["Subject"])
        self.assertIn("New product", sent["Subject"])
        self.assertEqual(sent["To"], "me@test")

    def test_no_restocks_sends_nothing(self):
        factory = mock.Mock()
        EmailNotifier("h", 465, "u", "p", "s", "r", smtp_factory=factory).send([])
        factory.assert_not_called()


class FakeClient:
    """Stands in for PoliteClient: url -> (status_code, final_url, html)."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        if url not in self.pages:
            return None
        code, final, text = self.pages[url]
        return mock.Mock(status_code=code, url=final, text=text)


SITE = "https://www.chromehearts.com/"


def item_list(*urls):
    elems = ",".join(f'{{"@type":"ListItem","url":"{u}"}}' for u in urls)
    return f'<script type="application/ld+json">{{"@type":"ItemList","itemListElement":[{elems}]}}</script>'


def product_page(name, availability="InStock", price="1200.00"):
    return ('<script type="application/ld+json">{"@type":"Product","name":"%s",'
            '"offers":{"price":"%s","availability":"http://schema.org/%s"}}</script>' % (name, price, availability))


class CatalogTests(unittest.TestCase):
    def test_category_status_rules(self):
        self.assertIs(category_status("/hoodies", SITE, 200, []), S.OUT_OF_STOCK)  # bounced home
        self.assertIs(category_status("/t-shirts", SITE + "t-shirts", 404, []), S.OUT_OF_STOCK)
        self.assertIs(category_status("/hoodies", SITE + "hoodies", 200, ["u"]), S.IN_STOCK)
        self.assertIs(category_status("/hoodies", SITE + "hoodies", 200, []), S.UNKNOWN)
        self.assertIs(category_status("/hoodies", SITE + "hoodies", 503, []), S.UNKNOWN)

    def test_reads_item_list(self):
        html = item_list(SITE + "socks/ch-logo-socks/176354XXXXXX349.html")
        self.assertEqual(product_urls_on_page(html, SITE + "socks"),
                         [SITE + "socks/ch-logo-socks/176354XXXXXX349.html"])

    def test_falls_back_to_links(self):
        html = '<a href="/hoodies/horseshoe-hoodie/ABC123XYZ.html">x</a><a href="/login">y</a>'
        self.assertEqual(product_urls_on_page(html, SITE),
                         [SITE + "hoodies/horseshoe-hoodie/ABC123XYZ.html"])

    def test_finds_category_paths_in_sitemap(self):
        xml = (f"<loc>{SITE}hat</loc><loc>{SITE}login</loc><loc>{SITE}</loc>"
               f"<loc>{SITE}on/demandware.store/Sites-ChromeHearts-Site/en_US/Search-Show?cgid=X</loc>")
        self.assertEqual(category_paths_in(xml, SITE), {"/hat"})

    def test_pop_up_drop_end_to_end(self):
        hoodie = SITE + "hoodies/horseshoe-hoodie/ABC123XYZ.html"
        offline = {SITE + "hoodies": (200, SITE, "<html>home</html>")}
        live = {
            SITE + "hoodies": (200, SITE + "hoodies", item_list(hoodie)),
            hoodie: (200, hoodie, product_page("HORSESHOE HOODIE")),
        }
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = StateStore(Path(tmp.name) / "s.db")
        self.addCleanup(store.close)
        notifier = mock.Mock()

        def check(pages):
            src = CatalogSource(SITE, ["/hoodies"], FakeClient(pages), discover=False)
            run_once(src, store, notifier)
            return [(a.event, a.item.name) for a in notifier.send.call_args.args[0]]

        self.assertEqual(check(offline), [])            # baseline: hoodies offline
        self.assertEqual(check(offline), [])            # still offline
        self.assertEqual(check(live), [                 # pop-up opens
            (Event.CATEGORY_LIVE, "Category /hoodies"),
            (Event.NEW_PRODUCT, "HORSESHOE HOODIE"),
        ])
        self.assertEqual(check(live), [])               # no repeats


class EndToEndTests(unittest.TestCase):
    """Mock file -> store -> detector -> notifier, the way the CLI runs it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mock_file = self.dir / "stock.json"
        self.store = StateStore(self.dir / "s.db")
        self.notifier = mock.Mock()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def write_stock(self, status):
        self.mock_file.write_text(json.dumps(
            {"products": [{"id": "ring", "name": "Ring", "status": status}]}))

    def test_alert_once_on_restock(self):  # first run is the baseline
        source = MockSource(self.mock_file)
        self.write_stock("out_of_stock")
        self.assertEqual(run_once(source, self.store, self.notifier), 0)
        self.write_stock("in_stock")
        self.assertEqual(run_once(source, self.store, self.notifier), 1)
        self.assertEqual(run_once(source, self.store, self.notifier), 0)  # no repeat

    def test_failed_email_is_retried_next_run(self):
        source = MockSource(self.mock_file)
        self.write_stock("out_of_stock")
        run_once(source, self.store, self.notifier)
        self.write_stock("in_stock")
        self.notifier.send.side_effect = OSError("smtp down")
        with self.assertRaises(OSError):
            run_once(source, self.store, self.notifier)
        self.notifier.send.side_effect = None
        self.assertEqual(run_once(source, self.store, self.notifier), 1)


class ConfigTests(unittest.TestCase):
    CFG = {"email": {"smtp_host": "smtp.163.com", "username": "you@163.com", "recipient": "you@163.com"}}

    def test_email_address_comes_from_environment(self):
        env = {"SMTP_PASSWORD": "code", "EMAIL_ADDRESS": "me@163.com"}
        with mock.patch.dict("os.environ", env, clear=True):
            n = build_notifier(self.CFG, dry_run=False)
        self.assertEqual((n.username, n.recipient, n.sender), ("me@163.com",) * 3)

    def test_gmail_address_picks_gmail_server(self):
        env = {"SMTP_PASSWORD": "abcd efgh ijkl mnop", "EMAIL_ADDRESS": "me@gmail.com"}
        with mock.patch.dict("os.environ", env, clear=True):
            n = build_notifier(self.CFG, dry_run=False)
        self.assertEqual((n.host, n.port, n.password), ("smtp.gmail.com", 465, "abcdefghijklmnop"))

    def test_alert_to_overrides_recipient(self):
        env = {"SMTP_PASSWORD": "x", "EMAIL_ADDRESS": "me@gmail.com", "ALERT_TO": "me@163.com"}
        with mock.patch.dict("os.environ", env, clear=True):
            n = build_notifier(self.CFG, dry_run=False)
        self.assertEqual((n.username, n.recipient), ("me@gmail.com", "me@163.com"))

    def test_placeholder_address_is_rejected(self):
        with mock.patch.dict("os.environ", {"SMTP_PASSWORD": "code"}, clear=True):
            with self.assertRaises(ConfigError):
                build_notifier(self.CFG, dry_run=False)


class UnreachableTests(unittest.TestCase):
    def test_everything_unknown_raises_and_keeps_state(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = StateStore(Path(tmp.name) / "s.db")
        self.addCleanup(store.close)
        store.save("a", S.OUT_OF_STOCK)
        source = mock.Mock()
        source.fetch.return_value = [snap("a", S.UNKNOWN), snap("b", S.UNKNOWN)]
        with self.assertRaises(SiteUnreachable):
            run_once(source, store, mock.Mock())
        self.assertEqual(store.load(), {"a": S.OUT_OF_STOCK})


if __name__ == "__main__":
    unittest.main()
