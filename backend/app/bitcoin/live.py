"""Live while someone is looking: the page calls `pulse` on open and about once a minute while its tab is visible.

    page visible ──► pulse ──► price older than ~45 s?  fetch it            (no AI, free)
                          ├──► news feeds older than ~5 min? fetch them      (no AI, free)
                          └──► new Bitcoin headlines since the last forecast (or none / too old)?
                                 and the last forecast is at least 30 min old, and none is running ──► start one

Nothing here runs on a timer of its own: a closed tab means no requests, so no fetches and no AI calls. That is the
whole trick, and it is why the scheduled background forecast is off by default (BITCOIN_BACKGROUND_FORECAST).
Synchronous (the API runs it in a worker thread); one pulse at a time per process, extra ones just report state.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.bitcoin import ingest
from app.config import Settings
from app.db import get_pool

log = logging.getLogger(__name__)
_lock = threading.Lock()
_last_news_fetch = 0.0  # monotonic seconds; per process, which is fine for a rate limit


def _state(conn) -> dict:
    price = conn.execute("select price_usd, fetched_at from bitcoin_prices order by fetched_at desc limit 1").fetchone()
    pred = conn.execute("select id, created_at from bitcoin_predictions order by created_at desc limit 1").fetchone()
    run = conn.execute(
        """select id, status from bitcoin_runs where (status = 'queued' and created_at > now() - interval '2 minutes')
              or (status = 'running' and started_at > now() - interval '15 minutes') order by created_at desc limit 1""").fetchone()
    fresh = 0
    if pred:
        fresh = conn.execute("select count(*) as n from bitcoin_news where btc_relevant and fetched_at > %s and published_at > %s",
                             (pred["created_at"], pred["created_at"] - timedelta(hours=6))).fetchone()["n"]
    return {"price": price, "pred": pred, "run": run, "fresh_news": fresh}


def pulse(s: Settings, enqueue: Callable[..., None]) -> dict:
    global _last_news_fetch
    now = datetime.now(timezone.utc)
    out = {"price_updated": False, "news_added": 0, "forecast": "current", "reason": None, "run_id": None}
    if not _lock.acquire(blocking=False):  # another tab is already refreshing: don't double-fetch
        return out | {"forecast": "busy"}
    try:
        with get_pool().connection() as conn:
            st = _state(conn)
        if st["price"] is None or (now - st["price"]["fetched_at"]).total_seconds() >= s.bitcoin_live_price_seconds:
            try:
                ingest.ingest_price(s)
                out["price_updated"] = True
            except Exception as exc:  # noqa: BLE001 - both exchanges down: keep showing the last price
                log.warning("live pulse: price fetch failed: %r", exc)
        if time.monotonic() - _last_news_fetch >= s.bitcoin_live_news_minutes * 60:
            _last_news_fetch = time.monotonic()
            try:
                out["news_added"] = int(ingest.ingest_news(s).get("inserted") or 0)
            except Exception as exc:  # noqa: BLE001
                log.warning("live pulse: news fetch failed: %r", exc)

        with get_pool().connection() as conn:
            st = _state(conn)
            pred, run = st["pred"], st["run"]
            if run:
                return out | {"forecast": "running", "run_id": run["id"]}
            age_min = None if pred is None else (now - pred["created_at"]).total_seconds() / 60
            if pred is None:
                why = "no forecast yet"
            elif age_min < s.bitcoin_live_min_minutes:
                return out | {"forecast": "current", "reason": f"forecast is {age_min:.0f} min old"} if not st["fresh_news"] else out | {
                    "forecast": "cooldown", "reason": f"{st['fresh_news']} new headline(s); next forecast after {s.bitcoin_live_min_minutes} min"}
            elif st["fresh_news"]:
                why = f"{st['fresh_news']} new Bitcoin headline(s) since the last forecast"
            elif age_min >= s.bitcoin_live_max_age_hours * 60:
                why = f"the last forecast is {age_min / 60:.0f} hours old"
            else:
                return out
            run_id = conn.execute("insert into bitcoin_runs (trigger) values ('live') returning id").fetchone()["id"]
        try:  # the run row is committed (the block above closed) before the job can look for it
            enqueue("app.tasks.generate_bitcoin_daily_insight", trigger="live", run_id=str(run_id))
        except Exception as exc:  # noqa: BLE001
            with get_pool().connection() as conn:
                conn.execute("update bitcoin_runs set status = 'failed', error = %s, finished_at = now() where id = %s", (str(exc)[:1000], run_id))
            return out | {"forecast": "error", "reason": str(exc)[:200]}
        log.info("live pulse started a forecast: %s", why)
        return out | {"forecast": "started", "reason": why, "run_id": run_id}
    finally:
        _lock.release()
