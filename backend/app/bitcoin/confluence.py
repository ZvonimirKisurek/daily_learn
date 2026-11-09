"""The Confluence Score: how many of five independent signals agree with a forecast's direction.

    four technical signals (computed here, from technicals.py) + one news signal (the model's own sentiment_score)
                                         │
             forecast direction (sign of predicted - current) ──► k = number of signals pointing that way (0..5)
                                         │
                       k ──► the allowed confidence band (per horizon) ──► validator + prompt + track-record regime

The SAME function is used in three places, so the rule cannot drift between them: the prompt shows the model the four
technical signals and the k it would get in each direction; the validator recomputes k from the model's actual output
(its predicted price and its sentiment_score) and rejects a confidence outside the band; and accuracy.py tags every past
forecast with its regime, so the track record can be read per regime ("how did we do in 5/5 setups?").

THE SIGNALS (+1 bullish, -1 bearish, 0 neutral). Each is a trend-following read, matching the 1-3 day horizons where
momentum dominates; the prompt handles the mean-reversion caveat for 7-14 days separately.
  SMA        price above BOTH SMA-7 and SMA-30 = bullish, below both = bearish, split or within 0.25% = neutral.
  MACD       MACD line and histogram on the same side of zero = that direction (trend and momentum agree). Mixed,
             e.g. below zero but above its signal (a downtrend whose momentum is recovering), or flat = neutral.
  RSI        >= 55 bullish, <= 45 bearish, between = neutral (ranging). Oversold still counts as bearish here.
  Bollinger  %B > 0.6 (riding the upper half) bullish, < 0.4 (lower half) bearish, between = neutral.
  News       sentiment_score >= +15 bullish, <= -15 bearish, between = neutral.

BANDS (confidence = probability the direction is right; it never goes below 50, so "2/5 or fewer" maps to 50-54).
  5/5: 80-95 | 4/5: 65-79 | 3/5: 50-64 | 0-2/5: 50-54
For 7d and 14d the FLOOR drops one tier (mean reversion: an oversold trend can bounce), the ceiling does not move.
"""
from __future__ import annotations

SIGNALS = ("SMA", "MACD", "RSI", "Bollinger", "news")
BANDS: dict[int, tuple[int, int]] = {5: (80, 95), 4: (65, 79), 3: (50, 64), 2: (50, 54), 1: (50, 54), 0: (50, 54)}
LONG_HORIZONS = frozenset({"7d", "14d"})
NEWS_DEAD_ZONE = 15
WORD = {1: "bullish", -1: "bearish", 0: "neutral"}


def _sign(x: float, dead_zone: float = 0.0) -> int:
    return 0 if abs(x) <= dead_zone else (1 if x > 0 else -1)


def technical_signals(t: dict) -> dict[str, int]:
    """The four technical votes from a Technicals.as_dict(). A missing indicator (short history, old forecast) is 0."""
    out = {"SMA": 0, "MACD": 0, "RSI": 0, "Bollinger": 0}
    p7, p30 = t.get("price_vs_sma7_pct"), t.get("price_vs_sma30_pct")
    if p7 is not None and p30 is not None:
        a, b = _sign(p7, 0.25), _sign(p30, 0.25)
        out["SMA"] = a if a == b else 0
    m, hist, price = t.get("macd"), t.get("macd_hist"), t.get("current_price") or 0
    if m is not None and hist is not None:
        flat = abs(m) < 0.003 * price and abs(hist) < 0.0005 * price
        out["MACD"] = 0 if flat or _sign(m) != _sign(hist) else _sign(m)
    if (r := t.get("rsi_14")) is not None:
        out["RSI"] = 1 if r >= 55 else -1 if r <= 45 else 0
    if (pb := t.get("bb_percent_b")) is not None:
        out["Bollinger"] = 1 if pb > 0.6 else -1 if pb < 0.4 else 0
    return out


def news_signal(sentiment_score: int) -> int:
    return _sign(sentiment_score, NEWS_DEAD_ZONE - 1)  # |score| >= 15 counts


def confluence(tech: dict[str, int], sentiment_score: int, direction: int) -> tuple[int, dict[str, int]]:
    """k = how many of the five signals point in `direction` (+1 up, -1 down), plus every signal's vote."""
    votes = {**tech, "news": news_signal(sentiment_score)}
    return sum(v == direction for v in votes.values()), votes


def band(k: int, horizon: str) -> tuple[int, int]:
    lo, hi = BANDS[k]
    if horizon in LONG_HORIZONS:
        lo = BANDS[max(k - 1, 0)][0]
    return lo, hi


def regime(k: int) -> str:
    """Coarse regime used to match the track record to the current setup (buckets keep the samples large enough)."""
    return "strong" if k >= 4 else "moderate" if k == 3 else "weak"


def describe(votes: dict[str, int]) -> str:
    return ", ".join(f"{name} {WORD[votes[name]]}" for name in SIGNALS if name in votes)
