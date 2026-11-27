"""Polymarket's public read APIs: no account and no key are needed to read prices. Trading is not done here.

    Gamma  https://gamma-api.polymarket.com   market metadata, token ids, fees, and the RESULT once a market resolves
    CLOB   https://clob.polymarket.com        live order books, and minute-level price history (also for past markets)

Every shape below was checked against live responses on 2026-11-27; trimmed copies are in tests/data/polymarket.
Quirks worth knowing:
  * Gamma returns `outcomes`, `outcomePrices` and `clobTokenIds` as JSON *strings*, not lists.
  * A resolved Up/Down market has closed = true and outcomePrices ["1", "0"] (Up won) or ["0", "1"] (Down won).
  * The CLOB book lists bids ascending and asks descending (best last). We take max/min instead of trusting the order.
  * Up/Down slugs are predictable: btc-updown-15m-<window start, unix seconds>; 5-minute windows use "5m".
  * Once a window resolves, the event carries eventMetadata {priceToBeat, finalPrice}: Chainlink's official numbers.
    A window's finalPrice is the next window's priceToBeat, so the live price to beat is known ~a minute after the
    previous window ends. (Coinbase differs from Chainlink by tens of dollars, which decides a 5-minute market.)
  * Gamma accepts several `slug` parameters in one request (answer order not guaranteed), which makes backfills ~10x cheaper.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from app.bitcoin.ingest import _get_json

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DURATIONS = (5, 15)


def _list(v) -> list:
    return v if isinstance(v, list) else json.loads(v or "[]")


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# ----------------------------------------------------------------------------------------------- Up/Down windows
def window_start(ts: float, minutes: int) -> int:
    """Unix seconds of the start of the window that contains `ts`."""
    step = minutes * 60
    return int(ts) // step * step


def updown_slug(minutes: int, start: int) -> str:
    return f"btc-updown-{minutes}m-{start}"


def fee_per_share(price: float, rate: float, exponent: float) -> float:
    """Taker fee for one share bought at `price`, from the market's feeSchedule {rate, exponent}. An ESTIMATE of
    Polymarket's crypto fee curve (largest near 50c, small near 0 or 1); it is only used to stay conservative."""
    return rate * (price * (1 - price)) ** exponent if 0 < price < 1 else 0.0


@dataclass(frozen=True)
class UpDown:
    slug: str
    minutes: int
    start: datetime
    end: datetime
    up_token: str
    down_token: str
    up_price: float | None      # Gamma's last outcome price for Up (a probability)
    price_to_beat: float | None # Chainlink at the start, official; published with the result (eventMetadata)
    final_price: float | None   # Chainlink at the end, official; it is also the NEXT window's price to beat
    closed: bool
    outcome: str | None         # 'up' / 'down' once resolved
    fee_rate: float
    fee_exponent: float
    min_order_size: float


def outcome_of(prices: list, closed: bool) -> str | None:
    if not closed or len(prices) != 2:
        return None
    up, down = (float(p) for p in prices)
    if up >= 0.99 and down <= 0.01:
        return "up"
    if down >= 0.99 and up <= 0.01:
        return "down"
    return None  # closed but not yet settled to 1/0


def parse_updown(event: dict) -> UpDown:
    m = event["markets"][0]
    minutes = int(str((m.get("cryptoMarketConfig") or {}).get("duration") or event["slug"].split("-")[2]).rstrip("m"))
    outcomes = [o.lower() for o in _list(m["outcomes"])]
    if outcomes != ["up", "down"]:
        raise ValueError(f"unexpected outcomes {outcomes} in {event['slug']}")
    tokens, prices = _list(m["clobTokenIds"]), _list(m.get("outcomePrices"))
    fees = m.get("feeSchedule") or {}
    closed = bool(m.get("closed") or event.get("closed"))
    meta = event.get("eventMetadata") or {}
    num = (lambda v: float(v) if v not in (None, "") else None)
    return UpDown(
        slug=event["slug"], minutes=minutes, start=_ts(m["eventStartTime"]), end=_ts(m["endDate"]),
        up_token=str(tokens[0]), down_token=str(tokens[1]), up_price=float(prices[0]) if prices else None,
        price_to_beat=num(meta.get("priceToBeat")), final_price=num(meta.get("finalPrice")),
        closed=closed, outcome=outcome_of(prices, closed),
        fee_rate=float(fees.get("rate", 0)) if m.get("feesEnabled", True) else 0.0, fee_exponent=float(fees.get("exponent", 1)),
        min_order_size=float(m.get("orderMinSize") or 5),
    )


def get_events(client: httpx.Client, slugs: list[str]) -> list[dict]:
    """Events for these slugs (missing ones are simply absent). Batched: Gamma takes repeated `slug` parameters."""
    out: list[dict] = []
    for i in range(0, len(slugs), 20):
        resp = client.get(f"{GAMMA}/events", params=[("slug", s) for s in slugs[i:i + 20]])
        resp.raise_for_status()
        out.extend(resp.json())
    return out


def get_updown(client: httpx.Client, minutes: int, start: int) -> UpDown | None:
    events = get_events(client, [updown_slug(minutes, start)])
    return parse_updown(events[0]) if events else None


# ----------------------------------------------------------------------------------------------- CLOB
def best_prices(book: dict) -> tuple[float | None, float | None]:
    """(best bid, best ask) of a CLOB book; None for an empty side."""
    bids = [float(b["price"]) for b in book.get("bids") or []]
    asks = [float(a["price"]) for a in book.get("asks") or []]
    return (max(bids) if bids else None), (min(asks) if asks else None)


def get_book(client: httpx.Client, token: str) -> tuple[float | None, float | None]:
    return best_prices(_get_json(client, f"{CLOB}/book", token_id=token))


def price_history(client: httpx.Client, token: str, start: int, end: int) -> list[tuple[int, float]]:
    """Minute-level trade prices of one outcome token, [(unix seconds, price)]. Works for past, resolved markets too."""
    data = _get_json(client, f"{CLOB}/prices-history", market=token, startTs=start, endTs=end, fidelity=1)
    return [(int(h["t"]), float(h["p"])) for h in data.get("history", [])]


# ----------------------------------------------------------------------------------------------- daily "Bitcoin above ___"
def daily_slug(d: date) -> str:
    return f"bitcoin-above-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"


def parse_daily(event: dict) -> dict:
    """The daily ladder: one YES/NO market per target price, all settling at the same instant (noon ET, Binance 1m close)."""
    rows = []
    for m in event["markets"]:
        if m.get("closed"):
            continue
        try:
            strike = float(str(m.get("groupItemTitle", "")).replace(",", "").replace("$", ""))
        except ValueError:
            continue
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        if bid is None or ask is None or not (0 < float(ask) < 1):
            continue
        fees = m.get("feeSchedule") or {}
        rows.append({"strike": strike, "question": m.get("question", ""), "yes_bid": float(bid), "yes_ask": float(ask),
                     "fee_rate": float(fees.get("rate", 0)) if m.get("feesEnabled") else 0.0, "fee_exponent": float(fees.get("exponent", 1))})
    end = min((_ts(m["endDate"]) for m in event["markets"] if m.get("endDate")), default=None)
    return {"slug": event["slug"], "title": event.get("title", ""), "settle_at": end, "rows": sorted(rows, key=lambda r: r["strike"])}


def client(settings, timeout: float | None = None) -> httpx.Client:
    return httpx.Client(timeout=timeout or settings.http_timeout, headers={"User-Agent": settings.user_agent, "Accept": "application/json"})


def utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)
