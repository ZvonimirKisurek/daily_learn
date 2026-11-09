"""Technical indicators, computed deterministically in Python. Pure functions, no I/O.

WHY NOT LET THE MODEL COMPUTE THEM? LLMs are poor at arithmetic over long number lists: asked for "the 30-day moving
average" of 30 prices, a model produces a plausible-looking number that is often wrong. So every number the model
reasons about is computed here and handed over as a fact. The model interprets the numbers but never calculates them.

The indicators, briefly:
  SMA-n         simple moving average of the last n closes. Price above SMA-30 is the classic "uptrend" read.
  RSI-14        Wilder's Relative Strength Index, 0-100. Above 70 is conventionally "overbought", below 30 "oversold".
  realised vol  standard deviation of daily log returns. σ_daily is the typical one-day move as a fraction of price.
  1-σ move(h)   price · σ_daily · √h. Under a random walk, about 68% of h-day outcomes land within ±1σ. This is
                the yardstick the model uses to size forecasts, and the validator uses to reject absurd ones.

  MACD(12,26,9) EMA-12 minus EMA-26 of closes (the "MACD line"), its own 9-period EMA (the "signal line"), and their
                difference (the "histogram"). An EMA weights recent prices geometrically (α = 2/(n+1)), so the gap
                between a fast and a slow EMA is a smoothed estimate of the CURRENT slope of price. MACD > 0: the fast
                average is above the slow one (uptrend). MACD > signal (histogram > 0): that slope is itself rising
                (momentum accelerating). A histogram shrinking toward zero means momentum is fading even if the trend
                has not turned yet. Because EMAs are recursive, the first values depend on how the series was seeded;
                the seed's influence decays like (1-α)^t, so ~3x the slow period (≈80 days) of history makes the
                numbers essentially seed-independent. That is why bitcoin_history_days defaults to 120.
  Bollinger     a 20-day SMA ± 2 standard deviations of closes. If closes were normal around the mean, ~95% would lie
  (20, 2)       inside; real prices are fat-tailed, so touches are more common, but the bands still give a
                volatility-scaled "normal range". %B = where price sits inside the bands (0 = lower, 1 = upper).
                Bandwidth = (upper - lower) / middle: it measures volatility itself. Volatility clusters and mean-
                reverts, so an unusually NARROW band (a "squeeze", bottom fifth of its own recent history) tends to be
                followed by a large move in either direction, and an unusually WIDE band by calmer trading.
  ATR-14        Wilder's Average True Range. True range = max(high - low, |high - prev close|, |low - prev close|):
                the day's full span including any gap from yesterday. ATR is its smoothed average, in DOLLARS. Unlike
                σ (close-to-close only) it sees intraday swings, so it is the better gauge of how far price really
                travels in a day. price ± ATR·√h is the "typical excursion" envelope the prompt gives per horizon (the
                √h is the random-walk scaling of spread with time). Without real OHLC (fallback feed) the true range
                degrades to |close - prev close| and the prompt says so.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

HORIZONS: tuple[tuple[str, int], ...] = (("1d", 1), ("3d", 3), ("7d", 7), ("14d", 14))
MIN_DAILY_SIGMA = 0.01  # floor: with very little or very calm data, never let the plausibility band collapse to nothing
SQUEEZE_PCTL, EXPANSION_PCTL = 20.0, 80.0  # Bollinger bandwidth percentiles that count as a squeeze / an expansion


class Candle(NamedTuple):
    """One UTC day. When the feed only has closes, open = high = low = close (see Technicals.ohlc_real)."""
    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Technicals:
    current_price: float
    change_1d_pct: float | None
    change_7d_pct: float | None
    change_30d_pct: float | None
    sma_7: float | None
    sma_30: float | None
    price_vs_sma7_pct: float | None
    price_vs_sma30_pct: float | None
    rsi_14: float | None
    daily_sigma: float  # fraction, e.g. 0.028 = 2.8% typical daily move (floored at MIN_DAILY_SIGMA)
    annualised_vol_pct: float
    high_30d: float | None
    low_30d: float | None
    one_sigma_move: dict[str, float]  # horizon key -> USD
    days_of_history: int
    # --- advanced indicators (None when there is not enough history) -------------------------------------------
    macd: float | None = None  # EMA12 - EMA26, USD
    macd_signal: float | None = None  # EMA9 of the MACD line
    macd_hist: float | None = None  # macd - signal
    macd_hist_prev: float | None = None  # yesterday's histogram: rising or fading momentum
    bb_upper: float | None = None
    bb_middle: float | None = None  # SMA-20
    bb_lower: float | None = None
    bb_percent_b: float | None = None  # 0 = on the lower band, 1 = on the upper band (can be outside 0..1)
    bb_bandwidth_pct: float | None = None  # (upper - lower) / middle, in %
    bb_bandwidth_pctl: float | None = None  # where today's bandwidth ranks in its own recent history, 0-100
    atr_14: float | None = None  # USD
    atr_pct: float | None = None  # ATR as % of price
    atr_envelope: dict[str, tuple[float, float]] | None = None  # horizon -> (price - ATR·√h, price + ATR·√h)
    ohlc_real: bool = False  # False: ATR approximated from closes because the feed had no highs/lows

    def as_dict(self) -> dict:
        return asdict(self)


def _pct(a: float, b: float) -> float:
    return round((a / b - 1) * 100, 2)


def sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI: seed with a simple average of the first `period` gains/losses, then exponential smoothing."""
    if len(closes) < period + 1:
        return None
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1)


def ema(values: Sequence[float], n: int) -> list[float]:
    """Exponential moving average, seeded with the SMA of the first n values. Returns one value per input from index
    n-1 on (so len(values) - n + 1 values), or [] if there are fewer than n inputs."""
    if len(values) < n:
        return []
    alpha = 2 / (n + 1)
    out = [sum(values[:n]) / n]
    for v in values[n:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float, float] | None:
    """(macd, signal, histogram, previous histogram) for the last day, or None with fewer than slow + signal closes."""
    if len(closes) < slow + signal:
        return None
    e_fast, e_slow = ema(closes, fast), ema(closes, slow)
    line = [f - s for f, s in zip(e_fast[slow - fast:], e_slow)]  # align: both series end on the last close
    sig = ema(line, signal)
    hist = [m - s for m, s in zip(line[signal - 1:], sig)]
    return line[-1], sig[-1], hist[-1], hist[-2]


def bollinger(closes: Sequence[float], n: int = 20, k: float = 2.0, lookback: int = 90) -> dict | None:
    """Bands for the last day plus the percentile of today's bandwidth among the last `lookback` days' bandwidths.
    Uses the population standard deviation (ddof=0), as in Bollinger's original definition."""
    if len(closes) < n:
        return None
    arr = np.asarray(closes, dtype=float)
    windows = np.lib.stride_tricks.sliding_window_view(arr, n)[-lookback:]  # one row per day that has a full window
    mid, sd = windows.mean(axis=1), windows.std(axis=1)
    width = 2 * k * sd / mid * 100  # bandwidth in % for every day in the lookback
    upper, lower = mid[-1] + k * sd[-1], mid[-1] - k * sd[-1]
    span = upper - lower
    return {
        "upper": float(upper), "middle": float(mid[-1]), "lower": float(lower),
        "percent_b": float((arr[-1] - lower) / span) if span > 0 else 0.5,
        "bandwidth_pct": float(width[-1]),
        # share of past bandwidths at or below today's: 5 = tighter than 95% of recent days (a squeeze)
        "bandwidth_pctl": float((width <= width[-1]).mean() * 100) if len(width) >= 20 else None,
    }


def atr(candles: Sequence[Candle], n: int = 14) -> float | None:
    """Wilder's ATR: seed with the mean of the first n true ranges, then ATR_t = (ATR_{t-1}·(n-1) + TR_t) / n."""
    if len(candles) < n + 1:
        return None
    h = np.array([c.high for c in candles[1:]])
    lo = np.array([c.low for c in candles[1:]])
    prev = np.array([c.close for c in candles[:-1]])
    tr = np.maximum.reduce([h - lo, np.abs(h - prev), np.abs(lo - prev)])
    value = tr[:n].mean()
    for x in tr[n:]:
        value = (value * (n - 1) + x) / n
    return float(value)


def daily_sigma(closes: list[float], window: int = 30) -> float:
    """Sample standard deviation of daily log returns over the last `window` returns, floored."""
    tail = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return MIN_DAILY_SIGMA
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return max(math.sqrt(var), MIN_DAILY_SIGMA)


def with_spot(history: list[tuple[date, float]], current_price: float, today: date | None = None) -> list[tuple[date, float]]:
    """Daily closes with today's incomplete bar replaced (or appended) by the live spot price."""
    today = today or datetime.now(timezone.utc).date()
    base = history[:-1] if history and history[-1][0] >= today else history
    return [*base, (today, current_price)]


def candles_with_spot(candles: Sequence[Candle], current_price: float, today: date | None = None) -> list[Candle]:
    """Today's partial candle gets the live price as its close (and stretches its high/low to include it); if the feed
    has no candle for today yet, a one-price candle is appended."""
    today = today or datetime.now(timezone.utc).date()
    if candles and candles[-1].day >= today:
        c = candles[-1]
        return [*candles[:-1], Candle(today, c.open, max(c.high, current_price), min(c.low, current_price), current_price)]
    return [*candles, Candle(today, current_price, current_price, current_price, current_price)]


def as_candles(history: Sequence) -> list[Candle]:
    """Accept either Candles or (date, close) pairs (close-only feeds and older callers)."""
    return [h if isinstance(h, Candle) else Candle(h[0], h[1], h[1], h[1], h[1]) for h in history]


def compute(history: Sequence, current_price: float, today: date | None = None) -> Technicals:
    """`history` = daily Candles or (date, close) pairs (UTC days), oldest first. Today's incomplete bar is replaced by
    the live spot price, so every indicator reflects the price the user sees on screen."""
    raw = as_candles(history)
    candles = candles_with_spot(raw, current_price, today)
    closes = [c.close for c in candles]
    ohlc_real = any(c.high != c.low for c in raw)

    def change(days: int) -> float | None:
        return _pct(current_price, closes[-1 - days]) if len(closes) > days else None

    def r(v: float | None, nd: int = 2) -> float | None:
        return None if v is None else round(v, nd)

    s7, s30 = sma(closes, 7), sma(closes, 30)
    sigma = daily_sigma(closes)
    m = macd(closes)
    bb = bollinger(closes)
    a = atr(candles)
    return Technicals(
        current_price=round(current_price, 2),
        change_1d_pct=change(1), change_7d_pct=change(7), change_30d_pct=change(30),
        sma_7=round(s7, 2) if s7 else None, sma_30=round(s30, 2) if s30 else None,
        price_vs_sma7_pct=_pct(current_price, s7) if s7 else None,
        price_vs_sma30_pct=_pct(current_price, s30) if s30 else None,
        rsi_14=rsi(closes),
        daily_sigma=round(sigma, 5),
        annualised_vol_pct=round(sigma * math.sqrt(365) * 100, 1),
        high_30d=round(max(c.high for c in candles[-30:]), 2) if len(closes) >= 2 else None,
        low_30d=round(min(c.low for c in candles[-30:]), 2) if len(closes) >= 2 else None,
        one_sigma_move={k: round(current_price * sigma * math.sqrt(h), 2) for k, h in HORIZONS},
        days_of_history=len(closes),
        macd=r(m and m[0]), macd_signal=r(m and m[1]), macd_hist=r(m and m[2]), macd_hist_prev=r(m and m[3]),
        bb_upper=r(bb and bb["upper"]), bb_middle=r(bb and bb["middle"]), bb_lower=r(bb and bb["lower"]),
        bb_percent_b=r(bb and bb["percent_b"], 3), bb_bandwidth_pct=r(bb and bb["bandwidth_pct"]),
        bb_bandwidth_pctl=r(bb and bb["bandwidth_pctl"], 0),
        atr_14=r(a), atr_pct=r(a and a / current_price * 100, 3),
        atr_envelope={k: (round(current_price - a * math.sqrt(h), 2), round(current_price + a * math.sqrt(h), 2)) for k, h in HORIZONS} if a else None,
        ohlc_real=ohlc_real,
    )
