"""Bitcoin data ingestion: spot price, daily price history, and news from the RSS sources.

PRICE. CoinGecko's free public API is the primary source (no key needed, roughly 5-30 calls/min). Coinbase's public
endpoints are the fallback. Binance is deliberately not used: its API returns HTTP 451 to US-hosted servers (Render,
GitHub Actions), and that would fail without warning in production. Price APIs are queried directly with httpx rather
than through PoliteClient: they are JSON APIs meant to be called by programs, and PoliteClient fails closed when robots.txt
can't be fetched, which is the right default for scraping but not for an API.

NEWS. Each feed in sources/bitcoin_sources.yaml goes through the same PoliteClient and feed parser the main ingestion
pipeline uses, so the user agent, robots.txt rules, per-host throttling and backoff all apply. Articles are deduplicated
on sha256(canonical URL), so re-running is idempotent. One failing feed never fails the run: the error is logged and the
other feeds continue.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feedparser
import httpx
import yaml
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.bitcoin.technicals import Candle
from app.config import Settings, get_settings
from app.db import get_pool
from app.ingestion.http import PoliteClient
from app.ingestion.parsers import canonicalize_url, parse_feed_entries, url_hash

log = logging.getLogger(__name__)

COINGECKO = "https://api.coingecko.com/api/v3"
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
BTC_PATTERN = re.compile(r"\b(bitcoin|btc|satoshi|sats|spot etf|ibit|microstrategy|strategy inc|halving|lightning network)\b", re.I)


class PriceUnavailable(RuntimeError):
    """Every price provider failed. Nothing is predicted without a real price."""


# ------------------------------------------------------------------------------------------------ price
def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 500, 502, 503, 504)


_retry = retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(3), wait=wait_exponential_jitter(multiplier=2, max=20), reraise=True)


@_retry
def _get_json(client: httpx.Client, url: str, **params) -> object:
    resp = client.get(url, params=params or None)
    resp.raise_for_status()
    return resp.json()


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(timeout=settings.http_timeout, headers={"User-Agent": settings.user_agent, "Accept": "application/json"})


def _sane(price: float) -> float:
    # Guard against a provider returning garbage (0, null coerced, wrong unit). Generous bounds, purely a tripwire.
    if not (100 <= price <= 10_000_000):
        raise ValueError(f"implausible BTC price {price!r}")
    return float(price)


def fetch_spot_price(settings: Settings | None = None) -> tuple[float, str]:
    """Current BTC/USD price and the name of the provider that answered."""
    s = settings or get_settings()
    errors = []
    with _client(s) as c:
        try:
            data = _get_json(c, f"{COINGECKO}/simple/price", ids="bitcoin", vs_currencies="usd")
            return _sane(data["bitcoin"]["usd"]), "coingecko"
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next provider"
            errors.append(f"coingecko: {exc!r}")
        try:
            data = _get_json(c, COINBASE_SPOT)
            return _sane(float(data["data"]["amount"])), "coinbase"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"coinbase: {exc!r}")
    raise PriceUnavailable("; ".join(errors))


def fetch_daily_ohlc(days: int, settings: Settings | None = None) -> list[Candle]:
    """Daily OHLC candles (UTC days), oldest first, one per day. Empty list if every provider fails: the predictor
    degrades gracefully (fewer indicators) rather than refusing to run.

    Coinbase is tried FIRST here because it is the free source with true daily highs and lows (ATR needs them).
    CoinGecko's free history has closes only (its /ohlc endpoint returns 4-day candles beyond 30 days), so as the
    fallback it yields close-only candles (open = high = low = close) and technicals marks ATR as approximated."""
    s = settings or get_settings()
    with _client(s) as c:
        try:
            # Coinbase candles: [time, low, high, open, close, volume], newest first, at most 300 per request.
            rows = _get_json(c, COINBASE_CANDLES, granularity=86400)
            out = sorted(Candle(datetime.fromtimestamp(r[0], tz=timezone.utc).date(), float(r[3]), float(r[2]), float(r[1]), float(r[4]))
                         for r in rows)
            if len(out) >= 2:
                return out[-days:]
        except Exception as exc:  # noqa: BLE001
            log.warning("coinbase candles failed: %r", exc)
        try:
            data = _get_json(c, f"{COINGECKO}/coins/bitcoin/market_chart", vs_currency="usd", days=days, interval="daily")
            by_day: dict[date, float] = {}
            for ms, price in data["prices"]:
                by_day[datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()] = float(price)  # last value per day wins
            if len(by_day) >= 2:
                return [Candle(d, p, p, p, p) for d, p in sorted(by_day.items())]
        except Exception as exc:  # noqa: BLE001
            log.warning("coingecko history failed: %r", exc)
    return []


def save_price(price: float, source: str) -> dict:
    with get_pool().connection() as conn:
        return conn.execute(
            "insert into bitcoin_prices (price_usd, source) values (%s, %s) returning id, price_usd, source, fetched_at",
            (round(price, 2), source)).fetchone()


def ingest_price(settings: Settings | None = None) -> dict:
    price, source = fetch_spot_price(settings)
    row = save_price(price, source)
    log.info("btc price %.2f from %s", price, source)
    return row


# ------------------------------------------------------------------------------------------------ news
class BitcoinSource(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(max_length=120)
    url: str
    weight: float = Field(default=1.0, ge=0, le=2)
    enabled: bool = True
    max_items: int = Field(default=30, ge=1, le=100)


def load_sources(settings: Settings | None = None) -> list[BitcoinSource]:
    s = settings or get_settings()
    path = Path(s.bitcoin_sources_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path  # relative to backend/, wherever the process was started
    specs = [BitcoinSource(**e) for e in (yaml.safe_load(path.read_text()) or {}).get("sources", [])]
    slugs = [x.slug for x in specs]
    if len(slugs) != len(set(slugs)):
        raise ValueError(f"duplicate slug in {path}")
    return [x for x in specs if x.enabled]


def clean_summary(summary: str, title: str, source_name: str) -> str:
    """Some feeds (Bitcoin Magazine, WordPress sites) wrap the summary as '<Source> <Title> <text> This post <Title>
    appeared first on <Source>.' Strip that so the prompt carries content, not boilerplate."""
    s = summary.strip()
    for prefix in (source_name, title):
        if prefix and s.lower().startswith(prefix.lower()):
            s = s[len(prefix):].lstrip(" :-–—")
    s = re.split(r"\s(?:This post|The post)\s", s, maxsplit=1)[0]
    return s.strip()[:1200]


async def _fetch_source(client: PoliteClient, src: BitcoinSource) -> list[dict]:
    resp = await client.get(src.url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.bozo_exception!r}")
    now = datetime.now(timezone.utc)
    rows = []
    for item in parse_feed_entries(parsed, src.max_items):
        canon = canonicalize_url(item.url)
        published = item.published_at or now
        rows.append({
            "source_name": src.name, "title": item.title, "url": item.url, "url_hash": url_hash(canon),
            "summary": clean_summary(item.summary, item.title, src.name), "published_at": min(published, now),  # future-dated entries are clamped to now
            "btc_relevant": bool(BTC_PATTERN.search(f"{item.title} {item.summary}")),
        })
    return rows


async def _fetch_all(sources: list[BitcoinSource], settings: Settings) -> tuple[list[dict], dict[str, str]]:
    client = PoliteClient(settings.user_agent, timeout=settings.http_timeout, max_retries=settings.http_max_retries,
                          min_interval=settings.host_min_interval)
    try:
        results = await asyncio.gather(*(_fetch_source(client, s) for s in sources), return_exceptions=True)
    finally:
        await client.aclose()
    rows, errors = [], {}
    for src, res in zip(sources, results):
        if isinstance(res, BaseException):
            log.warning("bitcoin source %s failed: %r", src.slug, res)
            errors[src.slug] = repr(res)[:300]
        else:
            rows.extend(res)
    return rows, errors


def save_news(rows: list[dict]) -> int:
    if not rows:
        return 0
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """insert into bitcoin_news (source_name, title, url, url_hash, summary, btc_relevant, published_at)
                   values (%(source_name)s, %(title)s, %(url)s, %(url_hash)s, %(summary)s, %(btc_relevant)s, %(published_at)s)
                   on conflict (url_hash) do nothing""", rows)
            # executemany's rowcount is the total across all statements in psycopg 3; conflicts count as 0
            return max(cur.rowcount, 0)


def ingest_news(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    sources = load_sources(s)
    rows, errors = asyncio.run(_fetch_all(sources, s))
    inserted = save_news(rows)
    log.info("bitcoin news: %d fetched, %d new, %d sources failed", len(rows), inserted, len(errors))
    return {"sources": len(sources), "fetched": len(rows), "inserted": inserted, "errors": errors}


def recent_news(hours: int, limit: int, max_per_source: int) -> list[dict]:
    """News for the prompt: recent, bitcoin-specific first, then newest, with a per-source diversity cap."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """select id, source_name, title, url, summary, btc_relevant, published_at from bitcoin_news
               where published_at >= %s order by btc_relevant desc, published_at desc limit 300""", (since,)).fetchall()
    picked, per_source = [], {}
    for r in rows:
        if per_source.get(r["source_name"], 0) >= max_per_source:
            continue
        per_source[r["source_name"]] = per_source.get(r["source_name"], 0) + 1
        picked.append(r)
        if len(picked) >= limit:
            break
    return picked
