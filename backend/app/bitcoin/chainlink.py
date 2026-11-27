"""Chainlink BTC/USD, live, from Polymarket's real-time data socket: the price the Up/Down markets actually settle on.

    wss://ws-live-data.polymarket.com   subscribe {"topic": "crypto_prices_chainlink", "filters": {"symbol": "btc/usd"}}
    messages: {"topic", "type", "timestamp", "payload": {"symbol": "btc/usd", "data": [{"timestamp": ms, "value": usd}, ...]}}
              (the first carries the last ~minute; single updates may come as payload {"symbol", "timestamp", "value"})
    keep-alive: send "PING" every few seconds.
    Re-sending the subscribe message on the same connection returns a fresh snapshot (newest tick ~1-2 s old). Observed
    behind some proxies: after the first snapshot no updates arrive. So whenever no new tick has come for RESUB_S seconds,
    the feed simply subscribes again; where updates do stream, it never needs to.

Exchanges differ from Chainlink by tens of dollars, which decides a 5-minute market, so the live loop prefers this
feed for "price now" and "price to beat", and falls back to Coinbase (corrected by the measured gap) if it is down.
Runs in its own thread with its own asyncio loop, reconnecting forever.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from bisect import bisect_right

log = logging.getLogger(__name__)
URL = "wss://ws-live-data.polymarket.com"
STALE_S = 15
RESUB_S = 5


class Feed:
    def __init__(self, symbol: str = "btc/usd", keep_s: int = 3 * 3600) -> None:
        self.symbol, self.keep_s = symbol, keep_s
        self._t: list[int] = []        # unix seconds, ascending
        self._v: list[float] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.connected = False

    # -- data
    def ingest(self, msg: dict) -> int:
        """Add the ticks in one socket message; returns how many were new."""
        p = msg.get("payload") or {}
        if p.get("symbol") not in (None, self.symbol):
            return 0
        rows = p.get("data") if isinstance(p.get("data"), list) else [p] if "value" in p else []
        n = 0
        with self._lock:
            for r in rows:
                try:
                    ts, v = int(r["timestamp"]) // 1000, float(r["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                i = bisect_right(self._t, ts)
                if i and self._t[i - 1] == ts:
                    self._v[i - 1] = v
                    continue
                self._t.insert(i, ts)
                self._v.insert(i, v)
                n += 1
            cut = bisect_right(self._t, (self._t[-1] if self._t else 0) - self.keep_s)
            del self._t[:cut], self._v[:cut]
        return n

    def latest(self, now: float | None = None) -> float | None:
        now = now or time.time()
        with self._lock:
            return self._v[-1] if self._t and now - self._t[-1] <= STALE_S else None

    def at(self, ts: float, tolerance: int = 3) -> float | None:
        """The price at second `ts` (the last tick at or before it, if no older than `tolerance` seconds)."""
        with self._lock:
            i = bisect_right(self._t, int(ts)) - 1
            return self._v[i] if i >= 0 and ts - self._t[i] <= tolerance else None

    def twap(self, end: float, seconds: int = 60) -> float | None:
        """Mean of the ticks in (end - seconds, end]; None unless most of that span was received."""
        with self._lock:
            lo, hi = bisect_right(self._t, int(end) - seconds), bisect_right(self._t, int(end))
            vals = self._v[lo:hi]
        return sum(vals) / len(vals) if len(vals) >= seconds * 0.6 else None

    # -- connection
    def start(self) -> Feed:
        threading.Thread(target=lambda: asyncio.run(self._run()), name="chainlink", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    async def _run(self) -> None:
        import websockets

        backoff = 1
        sub = json.dumps({"action": "subscribe", "subscriptions": [{"topic": "crypto_prices_chainlink", "type": "*",
                                                                    "filters": json.dumps({"symbol": self.symbol})}]})
        while not self._stop.is_set():
            try:
                async with websockets.connect(URL, open_timeout=15, ping_interval=None) as ws:
                    await ws.send(sub)
                    self.connected, backoff = True, 1
                    last_ping = last_new = last_sub = time.time()
                    while not self._stop.is_set():
                        now = time.time()
                        if now - last_new >= RESUB_S and now - last_sub >= RESUB_S:
                            await ws.send(sub)  # no fresh ticks: ask for a new snapshot
                            last_sub = now
                        elif now - last_ping >= 5:
                            await ws.send("PING")
                            last_ping = now
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 2)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(raw, str) and raw.lstrip().startswith("{") and self.ingest(json.loads(raw)):
                            last_new = time.time()
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                log.warning("chainlink feed disconnected: %r (retrying in %ss)", exc, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
