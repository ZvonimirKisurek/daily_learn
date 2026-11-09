"""Scoring past forecasts against what actually happened: the feedback half of the self-correcting loop.

    bitcoin_predictions (made h days ago) ──► actual price at created_at + h ──► per-horizon error metrics ──► prompt

WHICH FORECASTS ARE SCORED. A forecast can only be scored once its target date has passed, so "the last 14 days" means
the forecasts whose TARGET fell in the last 14 days (window_days), not the forecasts MADE in the last 14 days (a 14-day
forecast made 5 days ago has nothing to compare against yet). Every horizon is therefore judged over the same recent
calendar window, which is what you want when asking "how have I been doing lately?".

WHERE THE ACTUAL PRICE COMES FROM. First choice: the bitcoin_prices row (one per spot fetch) closest to the exact target
instant, within ±tolerance_hours. A scheduled daily run lands roughly 24h after the previous one, so this usually
matches to within minutes. Fallback: the daily close of the target's UTC date from the candle history the predictor
already fetched. A forecast with neither is skipped, not guessed.

THE METRICS (per horizon), and why each one is there:
  mape_pct          Mean Absolute Percentage Error, mean(|predicted - actual| / actual) · 100. How far off the price
                    targets were, in % of price. Scale-free, so 1d and 14d are comparable in units, but NOT in
                    difficulty: a random walk's expected error grows like σ·√h, so 14d MAPE is naturally ~3.7x the 1d.
  naive_mape_pct    the MAPE that "no change" (predict today's price) would have scored on the SAME forecasts. This is
                    the benchmark that makes MAPE interpretable: 3% sounds bad, but if no-change scored 3.4% the model
                    was actually adding value.
  skill             1 - mape / naive_mape. > 0: better than the random walk; < 0: worse (the model's moves were noise
                    that hurt). This is the number that should drive how far the model moves away from the base rate.
  hit_rate_pct      share of forecasts whose DIRECTION was right (predicted and actual on the same side of the price at
                    forecast time). This is exactly what `confidence` claims to measure.
  mean_confidence   the average confidence the model stated on those forecasts.
  calibration_gap   mean_confidence - hit_rate_pct. A calibrated forecaster has a gap near 0: forecasts labelled "70%"
                    come true ~70% of the time. Positive = overconfident (said 68%, was right 50%): the fix is to lower
                    confidence. Negative = underconfident.
  bias_pct          mean((predicted - actual) / actual) · 100, the SIGNED error. Positive = targets were too high on
                    average (a bullish lean), negative = too bearish. MAPE says "how wrong"; bias says "wrong which way".

SAMPLE SIZE. With n forecasts the standard error of a hit rate is sqrt(p(1-p)/n): about ±13 percentage points at n=14
and ±22 at n=5. Small samples are mostly noise, so the prompt is told to discount them (below MIN_SAMPLES, ignore them).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import fmean

from app.bitcoin.technicals import HORIZONS
from app.db import get_pool

log = logging.getLogger(__name__)

MIN_SAMPLES = 5  # below this, the prompt treats the track record as "not enough data yet"


def score(samples: list[dict]) -> dict:
    """Metrics for one horizon. Each sample has current, predicted, actual (USD) and confidence (0-100). Pure function."""
    if not samples:
        return {"n": 0, "mape_pct": None, "naive_mape_pct": None, "skill": None, "hit_rate_pct": None, "mean_confidence": None,
                "calibration_gap": None, "bias_pct": None}
    ape = [abs(s["predicted"] - s["actual"]) / s["actual"] * 100 for s in samples]
    naive = [abs(s["current"] - s["actual"]) / s["actual"] * 100 for s in samples]
    signed = [(s["predicted"] - s["actual"]) / s["actual"] * 100 for s in samples]
    # A forecast is a hit when predicted and actual moved the same way from the price at forecast time. If the price
    # ended exactly unchanged there is no direction to be right about, so that forecast is left out of the hit rate.
    directional = [s for s in samples if s["actual"] != s["current"]]
    hits = [(s["predicted"] > s["current"]) == (s["actual"] > s["current"]) for s in directional]
    mape, naive_mape = fmean(ape), fmean(naive)
    hit_rate = fmean(hits) * 100 if hits else None
    conf = fmean(s["confidence"] for s in samples)
    return {
        "n": len(samples),
        "mape_pct": round(mape, 2),
        "naive_mape_pct": round(naive_mape, 2),
        "skill": round(1 - mape / naive_mape, 2) if naive_mape > 0 else None,
        "hit_rate_pct": None if hit_rate is None else round(hit_rate, 1),
        "mean_confidence": round(conf, 1),
        "calibration_gap": None if hit_rate is None else round(conf - hit_rate, 1),
        "bias_pct": round(fmean(signed), 2),
    }


def _matured(days: int, start: datetime, now: datetime, tolerance_hours: float) -> list[dict]:
    """Forecasts for horizon `days` whose target instant (created_at + days) lies in [start, now], each with the
    bitcoin_prices row nearest to that instant (within the tolerance), or null."""
    key = f"{days}d"  # from the fixed HORIZONS table, never user input, so formatting it into the SQL is safe
    sql = f"""
        select p.created_at, p.current_price, p.predicted_price_{key} as predicted, p.confidence_{key} as confidence,
               p.created_at + make_interval(days => %(days)s) as target, a.price_usd as actual
        from bitcoin_predictions p
        left join lateral (
            select bp.price_usd from bitcoin_prices bp
            where bp.fetched_at between p.created_at + make_interval(days => %(days)s) - make_interval(secs => %(tol)s)
                                    and p.created_at + make_interval(days => %(days)s) + make_interval(secs => %(tol)s)
            order by abs(extract(epoch from bp.fetched_at - (p.created_at + make_interval(days => %(days)s))))
            limit 1
        ) a on true
        where p.created_at + make_interval(days => %(days)s) between %(start)s and %(now)s
        order by p.created_at"""
    with get_pool().connection() as conn:
        return conn.execute(sql, {"days": days, "tol": tolerance_hours * 3600, "start": start, "now": now}).fetchall()


def get_past_accuracy(daily_closes: dict[date, float] | None = None, *, window_days: int = 14, tolerance_hours: float = 12,
                      now: datetime | None = None) -> dict:
    """Score every forecast whose target fell in the last `window_days`. `daily_closes` (UTC date -> close) is the
    fallback source of actual prices when no spot fetch landed near the target instant."""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=window_days)
    closes = daily_closes or {}
    horizons, fallback_used = {}, 0
    for key, days in HORIZONS:
        samples = []
        for row in _matured(days, start, now, tolerance_hours):
            actual = row["actual"]
            if actual is None:
                actual = closes.get(row["target"].astimezone(timezone.utc).date())
                fallback_used += actual is not None
            if actual is None:
                continue
            samples.append({"current": float(row["current_price"]), "predicted": float(row["predicted"]), "actual": float(actual),
                            "confidence": int(row["confidence"])})
        horizons[key] = score(samples)
    result = {"window_days": window_days, "min_samples": MIN_SAMPLES, "horizons": horizons, "daily_close_fallbacks": fallback_used}
    log.info("btc accuracy over %dd: %s", window_days,
             " ".join(f"{k}:n={v['n']},mape={v['mape_pct']},hit={v['hit_rate_pct']}" for k, v in horizons.items()))
    return result
