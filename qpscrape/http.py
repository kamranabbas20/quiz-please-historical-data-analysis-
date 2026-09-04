"""Polite HTTP client: shared session, rate limiting, exponential backoff."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 (+quizplease-archive-research)"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    """Serialises requests so that at least `delay` seconds separate them."""

    def __init__(self, delay: float = 0.5) -> None:
        self.delay = delay
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            gap = time.monotonic() - self._last
            if gap < self.delay:
                time.sleep(self.delay - gap)
            self._last = time.monotonic()


class Fetcher:
    """requests.Session wrapper with retries, backoff and robots.txt awareness."""

    def __init__(
        self,
        delay: float = 0.5,
        timeout: float = 30.0,
        retries: int = 4,
        user_agent: str = DEFAULT_UA,
        respect_robots: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ru,en;q=0.8,az;q=0.6",
        })
        if headers:
            self.session.headers.update(headers)
        self.limiter = RateLimiter(delay)
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}

    # -- robots -----------------------------------------------------------
    def _robots_for(self, url: str) -> RobotFileParser | None:
        root = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if root not in self._robots:
            rp = RobotFileParser()
            rp.set_url(urljoin(root, "/robots.txt"))
            try:
                rp.read()
            except Exception as exc:  # network/parse failure -> treat as absent
                log.warning("robots.txt unavailable for %s (%s)", root, exc)
                rp = None
            self._robots[root] = rp
        return self._robots[root]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        if rp is None:
            return True
        return rp.can_fetch(self.session.headers["User-Agent"], url)

    # -- requests ---------------------------------------------------------
    def request(self, method: str, url: str, **kw: Any) -> requests.Response | None:
        if not self.allowed(url):
            log.warning("robots.txt disallows %s -- skipping", url)
            return None
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.request(
                    method, url, timeout=self.timeout, **kw
                )
            except requests.RequestException as exc:
                last_exc = exc
                resp = None
            if resp is not None and resp.status_code not in RETRY_STATUS:
                return resp
            if attempt == self.retries:
                break
            backoff = (2 ** attempt) + random.uniform(0, 0.4)
            if resp is not None and resp.status_code == 429:
                backoff = max(backoff, float(resp.headers.get("Retry-After", 0) or 0))
            log.warning(
                "retry %s/%s for %s (%s) in %.1fs",
                attempt + 1, self.retries, url,
                resp.status_code if resp is not None else last_exc, backoff,
            )
            time.sleep(backoff)
        if last_exc:
            log.error("giving up on %s: %s", url, last_exc)
        return None

    def get(self, url: str, **kw: Any) -> requests.Response | None:
        return self.request("GET", url, **kw)

    def get_text(self, url: str, **kw: Any) -> str | None:
        resp = self.get(url, **kw)
        if resp is None or resp.status_code >= 400:
            if resp is not None:
                log.warning("HTTP %s for %s", resp.status_code, url)
            return None
        return resp.text

    def get_json(self, url: str, **kw: Any) -> Any | None:
        resp = self.get(url, **kw)
        if resp is None or resp.status_code >= 400:
            return None
        try:
            return resp.json()
        except ValueError:
            return None
