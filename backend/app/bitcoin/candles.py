"""1-minute BTC/USD candles from Coinbase Exchange (public, no key): the raw material of the short-term model.

Coinbase answers at most 300 candles per request as [time, low, high, open, close, volume], newest first, where `time`
is the START of the minute. A candle is only final once its minute is over, so features at time t use candles that
started at or before t - 60 (no peeking at a minute that has not finished).
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import numpy as np

from app.bitcoin.ingest import COINBASE_CANDLES, _get_json
from app.db import get_pool

log = logging.getLogger(__name__)
COINBASE_TICKER = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"


def fetch(client: httpx.Client, start: int, end: int) -> list[tuple]:
    """Candles with start <= time < end, oldest first: [(unix seconds, open, high, low, close, volume)]."""
    out: dict[int, tuple] = {}
    t = start // 60 * 60
    while t < end:
        stop = min(t + 300 * 60, end)
        for row in _get_json(client, COINBASE_CANDLES, granularity=60, start=t, end=stop - 60):
            ts, low, high, open_, close, vol = row
            if start <= int(ts) < end:
                out[int(ts)] = (int(ts), float(open_), float(high), float(low), float(close), float(vol))
        t = stop
    return [out[k] for k in sorted(out)]


def spot(client: httpx.Client) -> float:
    return float(_get_json(client, COINBASE_TICKER)["price"])


def save(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """insert into btc_candles_1m (t, open, high, low, close, volume) values (to_timestamp(%s), %s, %s, %s, %s, %s)
               on conflict (t) do update set open = excluded.open, high = excluded.high, low = excluded.low,
                                             close = excluded.close, volume = excluded.volume""", rows)
    return len(rows)


def sync(client: httpx.Client, start: int, end: int) -> int:
    return save(fetch(client, start, end))


@dataclass
class Series:
    """Closes of consecutive minutes, for fast lookups by time."""
    t: np.ndarray      # minute starts, unix seconds, ascending
    close: np.ndarray

    def last_closed(self, at: float) -> int:
        """Index of the newest candle that was FINISHED at time `at` (started at or before at - 60); -1 if none."""
        return bisect.bisect_right(self.t, at - 60) - 1

    def close_at(self, at: float) -> float | None:
        i = self.last_closed(at)
        return float(self.close[i]) if i >= 0 else None


def load(start: datetime, end: datetime) -> Series:
    with get_pool().connection() as conn:
        rows = conn.execute("select extract(epoch from t)::bigint as t, close from btc_candles_1m where t >= %s and t < %s order by t",
                            (start, end)).fetchall()
    return Series(np.array([r["t"] for r in rows], dtype=np.int64), np.array([float(r["close"]) for r in rows], dtype=float))


def latest_time() -> datetime | None:
    with get_pool().connection() as conn:
        r = conn.execute("select max(t) as t from btc_candles_1m").fetchone()
    return r["t"] if r else None


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())
