"""Command line entry point: python -m restock --help"""
import argparse
import logging
import random
import sys
import time
from pathlib import Path

from .config import ConfigError, build_notifier, build_source, load_config
from .detector import find_alerts
from .models import Alert, Event, ProductSnapshot, StockStatus
from .store import StateStore

MIN_INTERVAL_MINUTES = 5  # be polite to the website

log = logging.getLogger("restock")


class SiteUnreachable(Exception):
    """Nothing could be read at all: the site is down or is blocking us."""


def run_once(source, store, notifier) -> int:
    """One check. Returns how many alerts were sent."""
    previous = store.load()
    current = source.fetch()
    if current and all(s.status is StockStatus.UNKNOWN for s in current):
        raise SiteUnreachable(f"All {len(current)} pages came back unknown")
    alerts = find_alerts(previous, current)
    if not previous:
        log.info("First run: recording %d items as the baseline (no emails)", len(current))

    # Send first, save after: if the email fails, the next run still sees the
    # old state and tries again instead of silently losing the alert.
    notifier.send(alerts)
    for snap in current:
        store.save(snap.product_id, snap.status)

    for snap in current:
        log.info("%-45s %s", snap.name, snap.status.value)
    for alert in alerts:
        log.info("ALERT %s: %s", alert.event.value, alert.item.name)
    log.info("Alerts this run: %d", len(alerts))
    return len(alerts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Email me when a product is back in stock.")
    parser.add_argument("--config", default="config.yaml", help="path to config file")
    parser.add_argument("--once", action="store_true", help="check one time and exit")
    parser.add_argument("--interval", type=float, default=20,
                        help=f"minutes between checks (min {MIN_INTERVAL_MINUTES})")
    parser.add_argument("--dry-run", action="store_true", help="print the email instead of sending")
    parser.add_argument("--test-email", action="store_true",
                        help="send one sample alert email and exit (checks your SMTP settings)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        cfg = load_config(Path(args.config))
        source = build_source(cfg)
        notifier = build_notifier(cfg, args.dry_run)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.test_email:
        sample = ProductSnapshot("test", "TEST ITEM (ignore me)", cfg.get("source", {}).get(
            "base_url", ""), StockStatus.IN_STOCK, "$0")
        notifier.send([Alert(Event.NEW_PRODUCT, sample)])
        print("Test email sent.")
        return 0

    store = StateStore(cfg["_base_dir"] / cfg.get("state_db", "state.db"))
    try:
        if args.once:
            try:
                run_once(source, store, notifier)
            except SiteUnreachable as exc:
                # Non-zero exit: on GitHub Actions this marks the run failed,
                # and GitHub emails you, so a silent block doesn't go unnoticed.
                log.error("%s. The site may be down or blocking this machine.", exc)
                return 3
            return 0

        interval = max(args.interval, MIN_INTERVAL_MINUTES)
        while True:
            try:
                run_once(source, store, notifier)
            except Exception:  # keep the watcher alive; log and retry next round
                log.exception("Check failed")
            # Random jitter so requests don't hit at exactly the same second every time.
            sleep_s = interval * 60 * random.uniform(0.9, 1.1)
            log.info("Next check in %.0f minutes", sleep_s / 60)
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
