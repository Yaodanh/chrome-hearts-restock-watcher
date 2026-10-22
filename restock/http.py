"""A small HTTP client that behaves politely toward the website."""
import logging
import time
from typing import Optional
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)


class PoliteClient:
    """GET pages while respecting robots.txt and pausing between requests.

    - robots.txt is read once per site. If it can't be read, the site is skipped.
    - Waits `delay_seconds` between any two requests.
    - Returns None instead of raising, so one bad page never stops a whole check.
    """

    def __init__(self, user_agent: str, delay_seconds: float = 2.0,
                 timeout_seconds: float = 15.0, session=None):
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._robots = {}
        self._last_request = 0.0

    def _wait_turn(self) -> None:
        gap = time.monotonic() - self._last_request
        if self._last_request and gap < self.delay_seconds:
            time.sleep(self.delay_seconds - gap)
        self._last_request = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                self._wait_turn()
                resp = self.session.get(urljoin(base, "/robots.txt"), timeout=self.timeout_seconds)
                # No robots.txt published means "no restrictions".
                rp.parse([] if resp.status_code >= 400 else resp.text.splitlines())
            except requests.RequestException as exc:
                log.warning("Could not read robots.txt for %s (%s); skipping site", base, exc)
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return rp is not None and rp.can_fetch(self.user_agent, url)

    def get(self, url: str) -> Optional[requests.Response]:
        """Fetch a page. Returns the response (any status) or None if skipped/failed."""
        if not self.allowed(url):
            log.warning("robots.txt does not allow %s; skipping", url)
            return None
        try:
            self._wait_turn()
            return self.session.get(url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            log.warning("Failed to fetch %s: %s", url, exc)
            return None
