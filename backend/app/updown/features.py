"""What the model sees at one moment inside an Up/Down window. Pure functions: the same code builds training rows from
history and live rows from the current tick, so what was learned is exactly what is used.

The single most informative input is `z`: how far BTC is from the price to beat, measured in the volatility still
ahead. With 10 minutes left a $50 gap is noise; with 20 seconds left it is almost decided. If BTC moved like a random
walk, P(Up) would simply be Phi(z) (the "analytic" baseline). The model's job is to learn where reality differs from
that (momentum, volatility regimes, the 60-second TWAP settlement, the crowd's price), and the validation report says
honestly whether it managed to.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from app.bitcoin.candles import Series

FEATURES = ("z", "frac", "log_secs_left", "r1", "r5", "r15", "vol_ratio", "market_logit", "is5m",
            "tod_sin", "tod_cos", "claude_lean", "claude_sentiment")
DEFAULT_SIGMA = 0.0008  # ~0.08% per minute: BTC's typical 1-minute volatility, used until enough candles exist
_N = NormalDist()


def logit(p: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


def sigma_1m(s: Series, at: float, n: int = 60) -> float:
    i = s.last_closed(at)
    if i < n:
        return DEFAULT_SIGMA
    r = np.diff(np.log(s.close[i - n:i + 1]))
    sd = float(r.std(ddof=1))
    return sd if sd > 1e-6 else DEFAULT_SIGMA


@dataclass
class Regime:
    """Claude's latest daily view at any moment: its 1-day lean (-0.5..0.5, from direction x confidence) and the news
    sentiment it scored (-1..1). Slow context for a fast model: this is how the LLM's reading of the news reaches it."""
    t: list[float]
    lean: list[float]
    sentiment: list[float]

    def at(self, ts: float) -> tuple[float, float]:
        i = bisect.bisect_right(self.t, ts) - 1
        return (self.lean[i], self.sentiment[i]) if i >= 0 else (0.0, 0.0)


def regime_from_rows(rows: list[dict]) -> Regime:
    rows = sorted(rows, key=lambda r: r["created_at"])
    lean = []
    for r in rows:
        direction = 1 if float(r["predicted_price_1d"]) > float(r["current_price"]) else -1 if float(r["predicted_price_1d"]) < float(r["current_price"]) else 0
        lean.append(direction * (int(r["confidence_1d"]) - 50) / 100)
    return Regime([r["created_at"].timestamp() for r in rows], lean, [int(r["sentiment_score"]) / 100 for r in rows])


def build(*, at: float, start: float, end: float, minutes: int, price: float, price_to_beat: float, p_market: float,
          candles: Series, regime: Regime) -> tuple[np.ndarray, float]:
    """(feature vector in FEATURES order, the analytic random-walk probability of Up)."""
    sig = sigma_1m(candles, at)
    secs_left = max(end - at, 1.0)
    z = math.log(price / price_to_beat) / (sig * math.sqrt(secs_left / 60))
    z = min(max(z, -8.0), 8.0)

    def ret(minutes_back: int) -> float:
        past = candles.close_at(at - 60 * (minutes_back - 1))  # last closed candle `minutes_back` minutes ago
        return 0.0 if not past else min(max(math.log(price / past) / (sig * math.sqrt(minutes_back)), -6.0), 6.0)

    day = sigma_1m(candles, at, n=1440) if candles.last_closed(at) >= 1440 else sig
    hour = (at % 86400) / 3600
    lean, sentiment = regime.at(at)
    x = np.array([z, (at - start) / (end - start), math.log(secs_left), ret(1), ret(5), ret(15), math.log(sig / day),
                  logit(p_market), 1.0 if minutes == 5 else 0.0, math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
                  lean, sentiment], dtype=float)
    return x, _N.cdf(z)
