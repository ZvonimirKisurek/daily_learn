"""Sizing a daily binary bet ("BTC above $K at settlement": YES pays $1, NO pays $1 otherwise). Pure functions, no I/O.

    forecast (1d direction + confidence) ──► calibrate against the track record ──► P(price > K at settlement)
                                                                                          │
   the site's quote (UP pays 1.02x, DOWN pays 8.31x) ──► the market's own probability ──► blend with ours by earned TRUST
                                                                                          │
              edge per side = our probability - cost (1 / multiplier) ──► side (UP / DOWN / not yet: ask again in N hours)
                                                                                          │
                       fractional Kelly on the ACTIVE bankroll, capped ──► STAKE in dollars (long shots get small stakes)
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
    min_edge: float = 0.03     # delta_min: our probability must beat the cost (1 / multiplier) by this much
    kelly: float = 0.50        # lambda: half-Kelly
    f_max: float = 0.30        # never more than this share of the active bankroll on one bet
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


# ----------------------------------------------------------------------------------------------- the market's quote
def implied_up(up_x: float, down_x: float) -> tuple[float, float]:
    """A payout quote ("UP pays 1.02x, DOWN pays 8.31x") as the market's own probability of UP, plus its margin.
    A multiplier m costs 1/m per $1 of payout; the two costs add up to more than 1, and the excess is the house margin."""
    a, b = 1 / up_x, 1 / down_x
    return a / (a + b), a + b - 1


def blend(p_model: float, p_market: float, trust: float) -> float:
    """The probability that is actually bet on: the market's, moved toward the model's by `trust` (0..1). A market price
    is information too; `trust` is earned from settled days (advisor.track_record), so the system learns how far its
    own forecast deserves to overrule the crowd."""
    return min(max(p_market + trust * (p_model - p_market), P_FLOOR), 1 - P_FLOOR)


def trust_from_record(closer: int, n: int, lo: float = 0.25, hi: float = 0.75) -> float:
    """Share of settled days on which the model was closer to the outcome than the market (Laplace-smoothed: 50% with
    no history), kept inside [lo, hi] so neither side is ever ignored."""
    return round(min(max((closer + 1) / (n + 2), lo), hi), 3)


# ----------------------------------------------------------------------------------------------- the daily routine
LONG_SHOT = 0.35  # a side that wins less often than this is a long shot: small stake, big multiplier


def advise(p_up: float, up_x: float, down_x: float, active: float, params: Params = Params()) -> dict:
    """Side and STAKE (dollars) for one cycle, from the probability we believe and the site's payout multipliers.

    A side is worth betting when its chance of winning beats its cost (1 / multiplier) by `min_edge`. The stake is
    fractional Kelly for a bet that pays m-for-1, f* = (p*m - 1) / (m - 1), capped at `f_max` of the active bankroll.
    Kelly is what makes a long shot safe to take: a 30% chance at 8x gets a small stake, never the whole bankroll.
    If that is less than the site's smallest bet, the smallest bet is placed instead, but only while it stays within FULL
    Kelly and the cap: betting more than full Kelly shrinks a bankroll over time even when the edge is real."""
    sides = {"BUY_YES": (p_up, up_x), "BUY_NO": (1 - p_up, down_x)}
    edges = {k: p - 1 / m for k, (p, m) in sides.items()}
    need = {k: (round(1 / (p - params.min_edge), 2) if p - params.min_edge > 0.01 else None) for k, (p, _) in sides.items()}
    base = {"shares": 0, "cost": 0.0, "entry_price": None, "multiplier": None, "prob_win": None, "kelly_full": None, "fraction": 0.0,
            "take_profit": None, "stop_loss": None, "max_profit": 0.0, "expected_profit": 0.0, "expected_return": None,
            "long_shot": False, "edge_yes": round(edges["BUY_YES"], 4), "edge_no": round(edges["BUY_NO"], 4),
            "need_up_x": need["BUY_YES"], "need_down_x": need["BUY_NO"]}
    if active < params.min_trade:
        return base | {"action": "HALT", "reason": f"The active bankroll (${active:.2f}) is below the smallest bet the site allows "
                                                   f"(${params.min_trade:.2f}). Trading stops; the vault stays untouched."}
    action = max(edges, key=edges.get)
    win, m = sides[action]
    if edges[action] < params.min_edge:
        return base | {"action": "HOLD_CASH", "reason": f"No edge at these quotes: the better side is worth {win * 100:.0f}% and costs "
                                                        f"{100 / m:.0f}% ({m:.2f}x). At least {params.min_edge * 100:.0f} points of edge are needed."}
    f_star = (win * m - 1) / (m - 1)
    fraction = max(0.0, min(params.kelly * f_star, params.f_max))
    stake = math.floor(active * fraction * 100) / 100
    rounded_up = False
    if stake < params.min_trade:
        most = active * min(f_star, params.f_max)
        if params.min_trade > most:
            return base | {"action": "HOLD_CASH", "kelly_full": round(f_star, 4), "prob_win": round(win, 4), "multiplier": m,
                           "reason": f"There is an edge on {'UP' if action == 'BUY_YES' else 'DOWN'} ({m:.2f}x), but it justifies at most "
                                     f"${most:.2f} of your ${active:.2f}, and the smallest bet is ${params.min_trade:.2f}. "
                                     "Betting more than that loses money over time. If the site allows smaller bets, lower 'Smallest bet'."}
        stake, rounded_up = round(params.min_trade, 2), True
    long_shot = win < LONG_SHOT
    kind = (f"A long shot: it loses about {round((1 - win) * 10)} times in 10, but pays {m:.2f}x when it hits." if long_shot
            else f"It pays {m:.2f}x.")
    return base | {
        "action": action, "shares": int(stake * m), "cost": stake, "entry_price": round(1 / m, 4), "multiplier": m,
        "prob_win": round(win, 4), "kelly_full": round(f_star, 4), "fraction": round(stake / active, 4), "long_shot": long_shot,
        "take_profit": round(1 / m + params.take * (1 - 1 / m), 4), "stop_loss": round((1 - params.stop) / m, 4),
        "max_profit": round(stake * (m - 1), 2), "expected_profit": round(stake * (win * m - 1), 2), "expected_return": round(win * m - 1, 4),
        "reason": f"This side is worth {win * 100:.0f}% and the site sells it for {100 / m:.0f}%: an edge of {edges[action] * 100:.0f} points. {kind}"
                  + (" Kelly asked for less than the site's smallest bet, so the smallest bet is used." if rounded_up else ""),
    }


def recheck_hours(hours_left: float, forecast_age_hours: float, action: str) -> float | None:
    """When to ask again. Quotes and the forecast both move during the day, so a 'no bet' is a 'not yet': come back about
    halfway to settlement (at most 6 hours), sooner when the forecast is already old. None = wait for the next market."""
    if action in ("BUY_YES", "BUY_NO", "HALT") or hours_left < 2:
        return None
    wait = min(6.0, hours_left / 2)
    if forecast_age_hours > 4:
        wait = min(wait, 2.0)
    return round(max(wait, 0.5) * 2) / 2


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
