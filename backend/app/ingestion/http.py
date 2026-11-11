"""Polite async HTTP client: identifies itself, obeys robots.txt, throttles per host, backs off on 429/5xx."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)
RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with jitter: base*2^attempt, capped, scaled by U(0.5, 1)."""
    return min(cap, base * 2**attempt) * random.uniform(0.5, 1.0)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:  # HTTP-date form
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 20.0,
        max_retries: int = 4,
        min_interval: float = 1.0,
        host_intervals: dict[str, float] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.user_agent = user_agent
        self._robot_token = user_agent.split("/")[0]  # "MLE-Sprint"
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/html;q=0.8, */*;q=0.5"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._max_retries = max_retries
        self._min_interval = min_interval
        self._host_intervals = host_intervals or {}  # slower spacing for hosts that rate-limit (e.g. medium.com)
        self._sleep = sleep
        self._robots: dict[str, RobotFileParser | None] = {}  # None => disallow all (robots unreachable)
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_hit: dict[str, float] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self, host: str) -> None:
        async with self._host_locks.setdefault(host, asyncio.Lock()):
            gap = next((v for h, v in self._host_intervals.items() if host == h or host.endswith("." + h)), self._min_interval)
            wait = self._last_hit.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        async with self._robots_locks.setdefault(origin, asyncio.Lock()):
            if origin in self._robots:
                return self._robots[origin]
            parser: RobotFileParser | None
            try:
                resp = await self._client.get(f"{origin}/robots.txt")
                if resp.status_code >= 500:
                    parser = None  # RFC 9309: server error => assume disallowed
                elif resp.status_code >= 400:
                    parser = RobotFileParser()  # no robots.txt => everything allowed
                    parser.parse([])
                else:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser
            return parser

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        parser = await self._load_robots(f"{parts.scheme}://{parts.netloc}")
        return parser is not None and parser.can_fetch(self._robot_token, url)

    async def request(self, method: str, url: str, *, respect_robots: bool = True, **kwargs) -> httpx.Response:
        """Returns the final response (callers decide what to do with 304/4xx). Raises on transport failure after retries."""
        if respect_robots:
            parts = urlsplit(url)
            parser = await self._load_robots(f"{parts.scheme}://{parts.netloc}")
            if parser is None:
                raise RobotsDisallowed(f"{url} (robots.txt unreachable or 5xx; failing closed)")
            if not parser.can_fetch(self._robot_token, url):
                raise RobotsDisallowed(f"{url} (disallowed by robots.txt)")
        host = urlsplit(url).netloc
        for attempt in range(self._max_retries + 1):
            await self._throttle(host)
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise
                delay = backoff_delay(attempt)
                log.warning("transport error %s on %s; retry in %.1fs", exc.__class__.__name__, url, delay)
                await self._sleep(delay)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self._max_retries:
                delay = parse_retry_after(resp.headers.get("Retry-After")) or backoff_delay(attempt)
                delay = min(delay, 120.0)
                log.warning("HTTP %s on %s; retry in %.1fs", resp.status_code, url, delay)
                await self._sleep(delay)
                continue
            return resp
        raise AssertionError("unreachable")

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)
