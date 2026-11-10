"""Sizing a daily binary bet ("BTC above $K at settlement": YES pays $1, NO pays $1 otherwise). Pure functions, no I/O.

    forecast (1d direction + confidence) ──► calibrate against the track record ──► P(price > K at settlement)
                                                                                          │
                market YES ask / bid ──► edge = model probability - price ──► direction (YES / NO / hold)
                                                                                          │
                       fractional Kelly on the ACTIVE bankroll, capped ──► shares, take-profit, stop-loss
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
SUSPICIOUS_EDGE = 0.25  # an edge this large usually means a typo or a model blind spot, not free money


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
def advise(p_yes: float, yes_ask: float, yes_bid: float, active: float, params: Params = Params()) -> dict:
    """Direction, size and exit levels for one cycle. A NO share costs 1 - YES bid and wins with probability 1 - p."""
    edge_yes, edge_no = p_yes - yes_ask, yes_bid - p_yes
    base = {"edge_yes": round(edge_yes, 4), "edge_no": round(edge_no, 4), "shares": 0, "cost": 0.0, "entry_price": None,
            "prob_win": None, "kelly_full": None, "fraction": 0.0, "take_profit": None, "stop_loss": None,
            "max_profit": 0.0, "expected_profit": 0.0}
    if active < params.min_trade:
        return base | {"action": "HALT", "reason": f"The active bankroll (${active:.2f}) is below the minimum order "
                                                   f"(${params.min_trade:.2f}). Trading stops; the vault stays untouched."}
    if edge_yes >= params.min_edge:
        action, entry, win = "BUY_YES", yes_ask, p_yes
    elif edge_no >= params.min_edge:
        action, entry, win = "BUY_NO", 1 - yes_bid, 1 - p_yes
    else:
        best = max(edge_yes, edge_no)
        gap = (f"the model's edge on the best side is only {best * 100:.1f} points" if best > 0
               else "the market price is already at or beyond what the model thinks it is worth")
        return base | {"action": "HOLD_CASH", "reason": f"No edge: {gap}; at least {params.min_edge * 100:.0f} points are needed to bet."}
    base |= {"entry_price": round(entry, 4), "prob_win": round(win, 4)}
    expected_return = (win - entry) / entry
    if params.fee and expected_return <= params.fee / (1 - params.fee):
        return base | {"action": "HOLD_CASH", "reason": f"The expected return ({expected_return * 100:.1f}%) does not cover the "
                                                        f"{params.fee * 100:.1f}% fee."}
    f_star = (win - entry) / (1 - entry)
    fraction = max(0.0, min(params.kelly * f_star, params.f_max))
    capital = active * fraction
    base |= {"kelly_full": round(f_star, 4), "fraction": round(fraction, 4)}
    shares = int(capital / entry)
    if capital < params.min_trade or shares < 1:
        return base | {"action": "HOLD_CASH", "reason": f"There is an edge, but the bet size (${capital:.2f}) is below the "
                                                        f"minimum order (${params.min_trade:.2f})."}
    cost = shares * entry
    return base | {
        "action": action, "shares": shares, "cost": round(cost, 2),
        "take_profit": round(entry + params.take * (1 - entry), 4), "stop_loss": round(entry * (1 - params.stop), 4),
        "max_profit": round(shares * (1 - entry), 2), "expected_profit": round(shares * (win - entry), 2),
        "reason": f"The model gives this side {win * 100:.0f}% and the market sells it for {entry * 100:.0f}c: "
                  f"an edge of {(win - entry) * 100:.0f} points.",
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
