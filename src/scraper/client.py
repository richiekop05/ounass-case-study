"""HTTP client: robots.txt, rate limit, disk cache, retries and stop conditions.

Everything that touches the network goes through `Client.get`. One request at a
time, never faster than `min_interval` after the previous one, plus jitter.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx

SITE = "https://www.ounass.ae"


class StopScrape(Exception):
    """The site blocked us or kept failing. Stop the whole run, do not work around it."""


class FetchError(Exception):
    """A single URL could not be fetched (404 and similar). The run continues."""

    def __init__(self, url: str, status: int | None, msg: str = ""):
        super().__init__(f"{status} {url} {msg}".strip())
        self.url = url
        self.status = status


class DisallowedByRobots(FetchError):
    def __init__(self, url: str):
        super().__init__(url, None, "disallowed by robots.txt")


# --------------------------------------------------------------------------- robots


@dataclass
class RobotsRules:
    """robots.txt rules for one user agent, with Google-style `*` and `$` support.

    The longest matching rule wins; on a tie, Allow wins. Python's urllib.robotparser
    does plain prefix matching, which would miss rules like `Disallow: /*?*`.
    """

    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)
    crawl_delay: float | None = None

    @classmethod
    def parse(cls, text: str, user_agent: str) -> "RobotsRules":
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent and agents:
                    groups.append((agents, rules, delay))
                    agents, rules, delay = [], [], None
                agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if key in ("allow", "disallow") and value:
                rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    pass
        if agents:
            groups.append((agents, rules, delay))

        token = user_agent.split("/")[0].strip().lower()
        specific = [g for g in groups if any(a != "*" and a in token for a in g[0])]
        chosen = specific or [g for g in groups if "*" in g[0]]
        out = cls()
        for _, r, d in chosen:
            out.rules.extend(r)
            if d is not None:
                out.crawl_delay = max(out.crawl_delay or 0, d)
        return out

    @staticmethod
    def _to_regex(pattern: str) -> re.Pattern[str]:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
        return re.compile("^" + rx + ("$" if anchored else ""))

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._to_regex(pattern).match(target):
                cand = (len(pattern), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]


# --------------------------------------------------------------------------- cache


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    content_type: str
    from_cache: bool

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class DiskCache:
    """Every response body plus a small JSON sidecar, keyed by sha256 of the URL."""

    def __init__(self, root: Path):
        self.root = root

    def _paths(self, url: str) -> tuple[Path, Path]:
        h = hashlib.sha256(url.encode()).hexdigest()
        d = self.root / h[:2]
        return d / f"{h}.body", d / f"{h}.json"

    def get(self, url: str) -> Response | None:
        body, meta = self._paths(url)
        if not (body.exists() and meta.exists()):
            return None
        m = json.loads(meta.read_text())
        return Response(url, m["status"], body.read_bytes(), m.get("content_type", ""), True)

    def put(self, resp: Response) -> None:
        body, meta = self._paths(resp.url)
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(resp.content)
        meta.write_text(json.dumps({
            "url": resp.url,
            "status": resp.status,
            "content_type": resp.content_type,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }))


# --------------------------------------------------------------------------- client


@dataclass
class ClientStats:
    requests: int = 0          # real network requests, including retries
    cache_hits: int = 0
    by_host: dict[str, int] = field(default_factory=dict)
    request_times: list[float] = field(default_factory=list)


class Client:
    def __init__(
        self,
        user_agent: str,
        cache_dir: Path = Path(".cache"),
        refresh: bool = False,
        min_interval: float = 1.0,
        jitter: tuple[float, float] = (0.5, 1.5),
        max_retries: int = 3,
        backoff_start: float = 5.0,
        max_consecutive_failures: int = 3,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ):
        if not user_agent or "example.com" in user_agent:
            raise ValueError("Set SCRAPER_USER_AGENT in .env with a real contact address.")
        self.user_agent = user_agent
        self.cache = DiskCache(cache_dir)
        self.refresh = refresh
        self.min_interval = min_interval
        self.jitter = jitter
        self.max_retries = max_retries
        self.backoff_start = backoff_start
        self.max_consecutive_failures = max_consecutive_failures
        self.clock = clock
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.stats = ClientStats()
        self._last_request: float | None = None
        self._consecutive_failures = 0
        self._robots: dict[str, RobotsRules] = {}
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
            follow_redirects=True,
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # -- robots -------------------------------------------------------------

    def robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            robots_url = origin + "/robots.txt"
            try:
                resp = self._fetch(robots_url, check_robots=False, expect_page=False)
                self._robots[origin] = RobotsRules.parse(resp.text, self.user_agent)
            except FetchError as e:
                if e.status is not None and 400 <= e.status < 500:
                    self._robots[origin] = RobotsRules()  # no robots.txt published: no rules
                else:
                    raise StopScrape(f"Could not read {robots_url}: {e}") from e
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        return self.robots_for(url).allowed(url)

    # -- public -------------------------------------------------------------

    def get(self, url: str, expect_page: bool = True) -> Response:
        """Fetch a URL (or serve it from cache). `expect_page` means an Ounass HTML page
        that must contain the embedded app state; anything else there is treated as a block."""
        if not self.allowed(url):
            raise DisallowedByRobots(url)
        return self._fetch(url, check_robots=True, expect_page=expect_page)

    # -- internals ----------------------------------------------------------

    def _wait_turn(self, crawl_delay: float | None) -> None:
        if self._last_request is None:
            return
        gap = self.min_interval + self.rng.uniform(*self.jitter)
        if crawl_delay:
            gap = max(gap, crawl_delay)
        remaining = self._last_request + gap - self.clock()
        if remaining > 0:
            self.sleep(remaining)

    def _fetch(self, url: str, check_robots: bool, expect_page: bool) -> Response:
        if not self.refresh:
            cached = self.cache.get(url)
            if cached is not None:
                self.stats.cache_hits += 1
                if cached.status >= 400:
                    raise FetchError(url, cached.status, "(cached)")
                return cached

        crawl_delay = self.robots_for(url).crawl_delay if check_robots else None
        attempt = 0
        while True:
            self._wait_turn(crawl_delay)
            self._last_request = self.clock()
            self.stats.requests += 1
            self.stats.request_times.append(self._last_request)
            host = urlsplit(url).netloc
            self.stats.by_host[host] = self.stats.by_host.get(host, 0) + 1
            try:
                r = self._http.get(url)
            except httpx.HTTPError as e:
                self._failure(f"network error on {url}: {e}")
                if attempt >= self.max_retries:
                    raise FetchError(url, None, str(e)) from e
                self.sleep(self.backoff_start * 2**attempt)
                attempt += 1
                continue

            resp = Response(url, r.status_code, r.content, r.headers.get("content-type", ""), False)

            if r.status_code == 403:
                raise StopScrape(f"403 Forbidden on {url}. The site is refusing this client; stopping.")
            if self._looks_like_block(resp, expect_page):
                raise StopScrape(f"Bot challenge or CAPTCHA page on {url}; stopping.")
            if r.status_code == 429 or r.status_code >= 500:
                self._failure(f"{r.status_code} on {url}")
                if attempt >= self.max_retries:
                    raise FetchError(url, r.status_code, "retries exhausted")
                self.sleep(self._retry_after(r) or self.backoff_start * 2**attempt)
                attempt += 1
                continue

            self._consecutive_failures = 0
            if r.status_code >= 400:
                # 404 and similar: remember it so a resumed run does not ask again.
                self.cache.put(resp)
                raise FetchError(url, r.status_code)
            self.cache.put(resp)
            return resp

    def _failure(self, msg: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.max_consecutive_failures:
            raise StopScrape(f"Three consecutive failures (last: {msg}); stopping.")

    @staticmethod
    def _looks_like_block(resp: Response, expect_page: bool) -> bool:
        if "html" not in resp.content_type:
            return False
        body = resp.text
        if re.search(r"captcha", body, re.I):
            return True
        return expect_page and resp.status == 200 and "window.initialState" not in body

    @staticmethod
    def _retry_after(r: httpx.Response) -> float | None:
        value = r.headers.get("retry-after")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
                return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                return None
