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
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

HORIZONS: tuple[tuple[str, int], ...] = (("1d", 1), ("3d", 3), ("7d", 7), ("14d", 14))
MIN_DAILY_SIGMA = 0.01  # floor: with very little or very calm data, never let the plausibility band collapse to nothing


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


def compute(history: list[tuple[date, float]], current_price: float, today: date | None = None) -> Technicals:
    """`history` = daily closes (UTC days), oldest first. Today's incomplete bar is replaced by the live spot price, so
    every indicator reflects the price the user sees on screen."""
    closes = [c for _, c in with_spot(history, current_price, today)]

    def change(days: int) -> float | None:
        return _pct(current_price, closes[-1 - days]) if len(closes) > days else None

    s7, s30 = sma(closes, 7), sma(closes, 30)
    sigma = daily_sigma(closes)
    last30 = closes[-30:]
    return Technicals(
        current_price=round(current_price, 2),
        change_1d_pct=change(1), change_7d_pct=change(7), change_30d_pct=change(30),
        sma_7=round(s7, 2) if s7 else None, sma_30=round(s30, 2) if s30 else None,
        price_vs_sma7_pct=_pct(current_price, s7) if s7 else None,
        price_vs_sma30_pct=_pct(current_price, s30) if s30 else None,
        rsi_14=rsi(closes),
        daily_sigma=round(sigma, 5),
        annualised_vol_pct=round(sigma * math.sqrt(365) * 100, 1),
        high_30d=round(max(last30), 2) if len(closes) >= 2 else None,
        low_30d=round(min(last30), 2) if len(closes) >= 2 else None,
        one_sigma_move={k: round(current_price * sigma * math.sqrt(h), 2) for k, h in HORIZONS},
        days_of_history=len(closes),
    )
