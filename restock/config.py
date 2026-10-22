"""Loads config.yaml. Secrets (the SMTP password) come only from the environment."""
import os
from pathlib import Path

import yaml

from .notifier import ConsoleNotifier, EmailNotifier
from .catalog import CatalogSource
from .http import PoliteClient
from .sources import MockSource, WebSource

DEFAULT_OUT_OF_STOCK_TEXTS = ["sold out", "out of stock", "notify me when available"]

# Mail domain -> (SMTP server, SSL port)
KNOWN_SMTP = {
    "gmail.com": ("smtp.gmail.com", 465),
    "googlemail.com": ("smtp.gmail.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
    "qq.com": ("smtp.qq.com", 465),
}


class ConfigError(Exception):
    pass


def load_config(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg["_base_dir"] = path.parent
    return cfg


def build_source(cfg: dict):
    src = cfg.get("source", {})
    kind = src.get("type", "mock")
    base = cfg["_base_dir"]
    if kind == "mock":
        return MockSource(base / src.get("mock_file", "data/mock_stock.json"))
    if kind == "web":
        products = src.get("products") or []
        if not products:
            raise ConfigError("source.type is 'web' but source.products is empty")
        return WebSource(
            products=products,
            out_of_stock_texts=src.get("out_of_stock_texts", DEFAULT_OUT_OF_STOCK_TEXTS),
            user_agent=src.get("user_agent", "restock-watcher/1.0 (personal use)"),
            delay_seconds=float(src.get("delay_seconds", 3)),
        )
    if kind == "catalog":
        client = PoliteClient(
            user_agent=src.get("user_agent", "restock-watcher/1.0 (personal use)"),
            delay_seconds=float(src.get("delay_seconds", 2)),
        )
        return CatalogSource(
            base_url=src.get("base_url", "https://www.chromehearts.com/"),
            categories=src.get("categories") or [],
            client=client,
            discover=bool(src.get("discover_categories", True)),
            check_products=bool(src.get("check_product_pages", True)),
        )
    raise ConfigError(f"Unknown source.type: {kind!r} (use 'catalog', 'web' or 'mock')")


def build_notifier(cfg: dict, dry_run: bool):
    email = dict(cfg.get("email") or {})
    # Subject prefix, e.g. "DEMO - Chrome Hearts" for demo runs.
    subject_tag = email.get("subject_tag", "Chrome Hearts")
    if dry_run:
        return ConsoleNotifier(subject_tag)
    # EMAIL_ADDRESS (e.g. from a GitHub secret) keeps your address out of a public repo.
    address = os.environ.get("EMAIL_ADDRESS", "").strip()
    if address:
        email["username"] = email["recipient"] = address
        email.pop("sender", None)
        # Pick the mail server from the address, so Gmail and 163 both just work.
        known = KNOWN_SMTP.get(address.rsplit("@", 1)[-1].lower())
        if known:
            email["smtp_host"], email["smtp_port"] = known
    # Optional: deliver alerts to a different inbox than the sending account.
    if os.environ.get("ALERT_TO", "").strip():
        email["recipient"] = os.environ["ALERT_TO"].strip()
    missing = [k for k in ("smtp_host", "username", "recipient")
               if not email.get(k) or email[k] == "you@163.com"]
    if missing:
        raise ConfigError(f"email section is missing: {', '.join(missing)}")
    # Gmail shows app passwords in groups ("abcd efgh ijkl mnop"); spaces aren't part of it.
    password = os.environ.get("SMTP_PASSWORD", "").replace(" ", "").strip()
    if not password:
        raise ConfigError("Set the SMTP_PASSWORD environment variable (never put it in config.yaml)")
    return EmailNotifier(
        host=email["smtp_host"],
        port=int(email.get("smtp_port", 465)),
        username=email["username"],
        password=password,
        sender=email.get("sender", email["username"]),
        recipient=email["recipient"],
        subject_tag=subject_tag,
    )
