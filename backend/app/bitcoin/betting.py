"""Sizing a daily binary bet ("BTC above $K at settlement": YES pays $1, NO pays $1 otherwise). Pure functions, no I/O.

    forecast (1d direction + confidence) ──► calibrate against the track record ──► P(price > K at settlement)
                                                                                          │
        the forecast's calibrated lean ──► direction (UP / DOWN / no bet) and the highest share price worth paying
                                                                                          │
                       fractional Kelly on the ACTIVE bankroll, capped ──► STAKE in dollars (the market price is not an input)
    balance reported each day ──► high-water mark ──► half of every new peak moves to the VAULT (never bet again)

WHY THE CALIBRATION STEP. Kelly sizing is only as good as the probability it is fed: betting as if you were right 80% of
the time when you are right 55% of the time loses money FASTER than betting small. The forecast's `confidence` comes
from a rule (how many of five signals agree), not from measured outcomes, so before it sizes a bet it is shrunk toward
50% by how well the 1-day forecasts actually did (accuracy.py): no proven edge means no directional view.

THE PROBABILITY MODEL. The forecast says "up/down over one day with confidence c". We read that as a drift on a random
walk with the measured daily volatility sigma: P(S_1 > S_0) = Phi(mu / sigma) = c  =>  mu = sigma * Phi^-1(c). Then for
any strike K and any time T (in days) left to settlement:  P(S_T > K) = Phi((ln(S/K) + mu*T) / (sigma*sqrt(T))).
With c = 50% this is the plain random walk; a strike at the current price gives back c for T = 1.

Nothing here is a guarantee: it is arithmetic on an experimental forecast. The rules exist to keep losses small.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

_N = NormalDist()
ACTIONS = ("BUY_YES", "BUY_NO", "HOLD_CASH", "HALT")
P_FLOOR = 0.02  # the probability is clamped to [2%, 98%]


@dataclass(frozen=True)
class Params:
    min_edge: float = 0.05     # delta_min: model probability must beat the price by this much
    kelly: float = 0.25        # lambda: quarter-Kelly
    f_max: float = 0.10        # never more than this share of the active bankroll on one bet
    alpha: float = 0.50        # share of every new equity peak swept into the vault
    take: float = 0.70         # take profit at this share of the maximum upside
    stop: float = 0.40         # stop loss when the position has lost this share of its cost
    min_trade: float = 1.00    # C_min: the platform's minimum order, also the "bankrupt" line for the active bankroll
    fee: float = 0.0           # phi: round-trip taker fee as a fraction


# ----------------------------------------------------------------------------------------------- probability
def calibration_shrink(track_1d: dict | None, min_samples: int = 5) -> tuple[float, str]:
    """How much of the forecast's stated edge over 50% to believe (0..1), from the scored 1-day forecasts."""
    n = (track_1d or {}).get("n") or 0
    hit, conf = (track_1d or {}).get("hit_rate_pct"), (track_1d or {}).get("mean_confidence")
    if n < min_samples or hit is None or conf is None:
        return 0.5, f"only {n} scored 1-day forecast(s) so far: half of the stated edge is trusted until there are {min_samples}"
    if hit <= 50:
        return 0.0, (f"1-day forecasts were right {hit:.0f}% of the time ({n} scored): no proven edge, "
                     "so direction is treated as a coin flip")
    shrink = min(1.0, (hit - 50) / max(conf - 50, 1e-6))
    return round(shrink, 3), f"1-day forecasts were right {hit:.0f}% of the time vs {conf:.0f}% stated ({n} scored)"


def prob_up_1d(predicted: float, current: float, confidence: int, shrink: float) -> tuple[float, float]:
    """(stated, calibrated) probability that BTC is higher one day after the forecast."""
    c = min(max(confidence, 50), 99) / 100
    stated = c if predicted > current else 1 - c if predicted < current else 0.5
    return stated, 0.5 + shrink * (stated - 0.5)


def prob_above(spot: float, strike: float, sigma_daily: float, hours: float, p_up_1d: float) -> float:
    """P(price at settlement > strike). See the module docstring for the model."""
    t = max(hours, 1 / 60) / 24
    mu = sigma_daily * _N.inv_cdf(min(max(p_up_1d, 0.01), 0.99))
    z = (math.log(spot / strike) + mu * t) / (sigma_daily * math.sqrt(t))
    # Real BTC returns have fatter tails than a normal curve, so the model is never allowed to be "certain".
    return min(max(_N.cdf(z), P_FLOOR), 1 - P_FLOOR)


# ----------------------------------------------------------------------------------------------- the daily routine
MIN_LEAN = 0.01  # a calibrated 1-day view within one point of 50% is a coin flip: no bet


def advise(p_yes: float, lean: float, active: float, params: Params = Params()) -> dict:
    """Direction and STAKE (in dollars) for one cycle, without knowing the market's share price (it moves all day).

    `lean` is the calibrated 1-day view minus 50%: positive means UP. The side follows the lean; the stake is what
    fractional Kelly allows at the WORST price still worth paying, `max_price` = P(win) - min_edge. Paying less than
    that only adds edge, so the stake is safe at any price up to it; above it there is no bet.
    Kelly at price q is (p - q) / (1 - q); at q = max_price that is min_edge / (1 - max_price)."""
    base = {"shares": 0, "cost": 0.0, "entry_price": None, "prob_win": None, "kelly_full": None, "fraction": 0.0,
            "take_profit": None, "stop_loss": None, "max_profit": 0.0, "expected_profit": 0.0}
    if active < params.min_trade:
        return base | {"action": "HALT", "reason": f"The active bankroll (${active:.2f}) is below the minimum order "
                                                   f"(${params.min_trade:.2f}). Trading stops; the vault stays untouched."}
    if abs(lean) < MIN_LEAN:
        return base | {"action": "HOLD_CASH", "reason": "No edge: the forecast has no proven lean up or down right now, "
                                                        "so a bet would be a coin flip minus fees."}
    action, win = ("BUY_YES", p_yes) if lean > 0 else ("BUY_NO", 1 - p_yes)
    max_price = round(win - params.min_edge - params.fee, 2)
    base |= {"prob_win": round(win, 4)}
    if max_price < 0.03:
        return base | {"action": "HOLD_CASH", "reason": f"The model gives this side only {win * 100:.0f}%: too unlikely to be worth a bet."}
    f_star = (win - max_price) / (1 - max_price)
    fraction = max(0.0, min(params.kelly * f_star, params.f_max))
    stake = math.floor(active * fraction * 100) / 100
    base |= {"entry_price": max_price, "kelly_full": round(f_star, 4), "fraction": round(fraction, 4)}
    if stake < params.min_trade:
        return base | {"action": "HOLD_CASH", "reason": f"There is a lean, but the bet size (${stake:.2f}) is below the "
                                                        f"minimum order (${params.min_trade:.2f})."}
    shares = stake / max_price  # at the worst acceptable price; a cheaper price buys more shares for the same stake
    return base | {
        "action": action, "shares": int(shares), "cost": stake,
        "max_profit": round(shares * (1 - max_price), 2), "expected_profit": round(shares * (win - max_price), 2),
        "reason": f"The model gives this side {win * 100:.0f}%. Bet ${stake:.2f}, and only while one share costs "
                  f"{max_price * 100:.0f}c or less: above that price the edge is gone, so skip the bet.",
    }


def settle(balance: float, vault: float, peak: float, alpha: float = 0.5) -> tuple[float, float, float, float]:
    """High-water-mark sweep. `balance` is total equity (active + vault). Returns (active, vault, peak, swept).
    Invariants: the vault never decreases; active = balance - vault, floored at 0 (no bailout from the vault)."""
    swept = 0.0
    if balance > peak:
        swept = round((balance - peak) * alpha, 2)
        vault, peak = vault + swept, balance
    return round(max(0.0, balance - vault), 2), round(vault, 2), round(peak, 2), swept


# ----------------------------------------------------------------------------------------------- during the day
def check_position(entry: float, sell_price: float, prob_win_now: float, shares: int, params: Params = Params()) -> dict:
    """Should an open position be sold now? `sell_price` is what the market pays for one of YOUR shares right now."""
    gain = sell_price - entry
    out = {"sell_price": round(sell_price, 4), "entry_price": round(entry, 4), "prob_win_now": round(prob_win_now, 4),
           "unrealized": round(shares * gain, 2), "shares": shares}
    if sell_price >= prob_win_now:
        return out | {"action": "SELL", "rule": "exit", "reason": f"The market pays {sell_price * 100:.0f}c for something the model now "
                                                                  f"values at {prob_win_now * 100:.0f}c: selling beats holding."}
    if gain >= params.take * (1 - entry):
        return out | {"action": "SELL", "rule": "take_profit", "reason": f"{params.take * 100:.0f}% of the maximum profit is already "
                                                                         "on the table: take it and skip the settlement coin toss."}
    if gain <= -params.stop * entry:
        return out | {"action": "SELL", "rule": "stop_loss", "reason": f"The position has lost {params.stop * 100:.0f}% of its cost: "
                                                                       "cut it before settlement."}
    return out | {"action": "HOLD", "rule": "hold", "reason": f"The model still values a share at {prob_win_now * 100:.0f}c, above the "
                                                              f"{sell_price * 100:.0f}c the market pays: keep holding."}
