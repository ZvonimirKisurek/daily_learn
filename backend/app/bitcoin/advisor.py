"""The bet advisor's I/O: live price, latest forecast, track record and bankroll state in; a stored recommendation out.

Synchronous on purpose (the API runs it in a worker thread): it makes one outbound price request and a few small queries,
and reuses the same sync helpers as the forecast job. All arithmetic lives in betting.py.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.bitcoin import accuracy, betting, ingest
from app.config import Settings
from app.db import get_pool

log = logging.getLogger(__name__)
STALE_FORECAST_HOURS = 12


class AdvisorError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def params(s: Settings) -> betting.Params:
    return betting.Params(min_edge=s.bitcoin_bet_min_edge, kelly=s.bitcoin_bet_kelly, f_max=s.bitcoin_bet_max_fraction,
                          alpha=s.bitcoin_bet_vault_share, take=s.bitcoin_bet_take_profit, stop=s.bitcoin_bet_stop_loss,
                          min_trade=s.bitcoin_bet_min_trade, fee=s.bitcoin_bet_fee)


def next_settlement(s: Settings, now: datetime | None = None) -> datetime:
    """The next daily settlement instant (e.g. 18:00 CET) strictly after `now`."""
    tz = ZoneInfo(s.bitcoin_bet_timezone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    at = local.replace(hour=s.bitcoin_bet_settle_hour, minute=0, second=0, microsecond=0)
    return (at if at > local else at + timedelta(days=1)).astimezone(timezone.utc)


def _spot(s: Settings) -> dict:
    """A fresh spot price if the exchanges answer, else the last stored one (flagged by its timestamp)."""
    try:
        return ingest.ingest_price(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("live BTC price unavailable for the bet advisor (%r); using the last stored price", exc)
        with get_pool().connection() as conn:
            row = conn.execute("select id, price_usd, source, fetched_at from bitcoin_prices order by fetched_at desc limit 1").fetchone()
        if row is None:
            raise AdvisorError(503, "No Bitcoin price is available yet. Press Refresh on the Bitcoin page first.") from None
        return row


def _model(s: Settings, spot: float, strike: float, hours: float) -> dict:
    """Everything the probability rests on, from the latest stored forecast and its scored track record."""
    with get_pool().connection() as conn:
        pred = conn.execute("""select id, created_at, current_price, predicted_price_1d, confidence_1d,
                                      inputs -> 'technicals' ->> 'daily_sigma' as sigma
                               from bitcoin_predictions order by created_at desc limit 1""").fetchone()
    if pred is None or pred["sigma"] is None:
        raise AdvisorError(409, "There is no Bitcoin forecast yet. Press Refresh on the Bitcoin page, wait for it to finish, then try again.")
    try:
        track = accuracy.get_past_accuracy(window_days=s.bitcoin_bet_calibration_days,
                                           tolerance_hours=s.bitcoin_accuracy_tolerance_hours)["horizons"]["1d"]
    except Exception:  # noqa: BLE001 - no track record just means the cautious default shrink
        log.exception("bet advisor: track record unavailable")
        track = None
    shrink, note = betting.calibration_shrink(track, accuracy.MIN_SAMPLES)
    stated, calibrated = betting.prob_up_1d(float(pred["predicted_price_1d"]), float(pred["current_price"]), pred["confidence_1d"], shrink)
    sigma = float(pred["sigma"])
    age = (datetime.now(timezone.utc) - pred["created_at"]).total_seconds() / 3600
    return {"prediction_id": pred["id"], "forecast_at": pred["created_at"], "forecast_age_hours": round(age, 1),
            "sigma_daily": sigma, "stated_up_1d": round(stated, 4), "shrink": shrink, "calibrated_up_1d": round(calibrated, 4),
            "calibration_note": note, "p_yes": round(betting.prob_above(spot, strike, sigma, hours, calibrated), 4)}


def track_record(s: Settings, days: int = 60) -> dict:
    """What the advisor has learned from its own settled days. For every past check-in that carried a quote, look up the
    real BTC price at settlement and ask: who was closer to what happened, the model or the market? The share of days
    the model won becomes `trust`, the weight its probability gets against the market's the next time."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            """select distinct on (b.detail -> 'inputs' ->> 'settle_at', b.detail -> 'inputs' ->> 'strike')
                      b.action, b.detail,
                      (select p.price_usd from bitcoin_prices p
                        where p.fetched_at between (b.detail -> 'inputs' ->> 'settle_at')::timestamptz - interval '90 minutes'
                                               and (b.detail -> 'inputs' ->> 'settle_at')::timestamptz + interval '90 minutes'
                        order by abs(extract(epoch from p.fetched_at - (b.detail -> 'inputs' ->> 'settle_at')::timestamptz)) limit 1) as settle_price
               from bitcoin_bets b
               where b.detail -> 'market' ? 'p_market' and (b.detail -> 'inputs' ->> 'settle_at')::timestamptz < now()
                 and b.created_at > now() - make_interval(days => %s)
               order by b.detail -> 'inputs' ->> 'settle_at', b.detail -> 'inputs' ->> 'strike', b.created_at desc""", (days,)).fetchall()
    n = closer = bets = wins = 0
    for r in rows:
        if r["settle_price"] is None:
            continue
        d = r["detail"]
        up = float(r["settle_price"]) > float(d["inputs"]["strike"])
        n += 1
        closer += abs(float(d["model"]["p_yes"]) - up) < abs(float(d["market"]["p_market"]) - up)
        if r["action"] in ("BUY_YES", "BUY_NO"):
            bets += 1
            wins += (r["action"] == "BUY_YES") == up
    trust = betting.trust_from_record(closer, n, s.bitcoin_bet_trust_min, s.bitcoin_bet_trust_max)
    note = ("No settled days with a quote yet: the forecast and the market's quote get equal weight." if n == 0 else
            f"On {n} settled day{'s' if n != 1 else ''}, the forecast was closer to the outcome than the market on {closer}"
            + (f"; {wins} of {bets} recommended bet{'s' if bets != 1 else ''} won" if bets else "") + ".")
    return {"n": n, "model_closer": closer, "bets": bets, "wins": wins, "trust": trust, "note": note}


def _decide(s: Settings, model: dict, up_x: float, down_x: float, active: float, p: betting.Params, record: dict) -> tuple[dict, dict]:
    """Quote + model + earned trust -> the recommendation and the market block stored with it."""
    p_market, margin = betting.implied_up(up_x, down_x)
    p_used = betting.blend(model["p_yes"], p_market, record["trust"])
    rec = betting.advise(p_used, up_x, down_x, active, p)
    return rec, {"up_x": up_x, "down_x": down_x, "p_market": round(p_market, 4), "margin": round(margin, 4), "p_used": round(p_used, 4),
                 "trust": record["trust"]}


def latest_state() -> dict | None:
    with get_pool().connection() as conn:
        return conn.execute("select * from bitcoin_bets order by created_at desc limit 1").fetchone()


def advise(s: Settings, *, balance: float, up_x: float, down_x: float, strike: float | None = None, min_bet: float | None = None,
           hours_to_settle: float | None = None, reset: bool = False) -> dict:
    """The check-in: settle the bankroll against the reported balance, then say which side to bet, how many dollars, or
    when to ask again. Inputs are what the site shows: the balance and the two payout multipliers (UP pays up_x, DOWN pays
    down_x). `strike` is the price to beat (default: the price now); `min_bet` is the site's smallest allowed bet."""
    now = datetime.now(timezone.utc)
    settle_at = now + timedelta(hours=hours_to_settle) if hours_to_settle else next_settlement(s, now)
    hours = (settle_at - now).total_seconds() / 3600
    p = params(s)
    if min_bet is not None:
        p = replace(p, min_trade=min_bet)

    prev = None if reset else latest_state()
    if prev is None:  # first check-in (or a reset): everything is active, nothing vaulted, the peak is where you start
        active, vault, peak, swept, pnl = round(balance, 2), 0.0, round(balance, 2), 0.0, None
    else:
        active, vault, peak, swept = betting.settle(balance, float(prev["vault"]), float(prev["peak"]), p.alpha)
        pnl = round(balance - float(prev["balance"]), 2)

    price = _spot(s)
    spot = float(price["price_usd"])
    strike = round(spot, 2) if strike is None else strike
    model = _model(s, spot, strike, hours)
    record = track_record(s)
    rec, market = _decide(s, model, up_x, down_x, active, p, record)
    wait = betting.recheck_hours(hours, model["forecast_age_hours"], rec["action"])
    tz = ZoneInfo(s.bitcoin_bet_timezone)
    rec["next_check"] = None if wait is None else {
        "hours": wait, "at": (now + timedelta(hours=wait)).isoformat(),
        "label": f"{(now + timedelta(hours=wait)).astimezone(tz):%H:%M} {s.bitcoin_bet_timezone}"}

    warnings = []
    if strike == round(spot, 2) and not 0.35 <= market["p_market"] <= 0.65:
        warnings.append(f"These quotes say the market gives UP about {market['p_market'] * 100:.0f}%, so its price to beat is far from "
                        "the current price. Enter the site's price to beat (the target), or this advice compares the wrong bet.")
    if abs(model["p_yes"] - market["p_market"]) >= 0.25:
        warnings.append("The forecast and the market disagree by a lot. That is more often a wrong target price or something the "
                        "forecast cannot see than easy money: double-check the target and the quotes.")
    if model["forecast_age_hours"] > STALE_FORECAST_HOURS:
        warnings.append(f"The forecast is {model['forecast_age_hours']:.0f} hours old. Press Refresh on the Bitcoin page and ask again for a current view.")
    if (now - price["fetched_at"]).total_seconds() > 900:
        warnings.append("The live price could not be fetched; the last stored price was used.")
    detail = {
        **rec,
        "inputs": {"balance": round(balance, 2), "strike": strike, "up_x": up_x, "down_x": down_x, "min_bet": p.min_trade, "hours_to_settle": round(hours, 2),
                   "settle_at": settle_at.isoformat()},
        "market": {"spot": spot, "spot_at": price["fetched_at"].isoformat(), **market},
        "record": record,
        "model": {k: (v.isoformat() if isinstance(v, datetime) else str(v) if k == "prediction_id" else v) for k, v in model.items()},
        "bankroll": {"balance": round(balance, 2), "active": active, "vault": vault, "peak": peak, "swept": swept, "pnl": pnl},
        "warnings": warnings,
    }
    with get_pool().connection() as conn:
        row = conn.execute(
            """insert into bitcoin_bets (prediction_id, balance, active, vault, peak, swept, pnl, is_reset, action, detail)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id, created_at""",
            (model["prediction_id"], round(balance, 2), active, vault, peak, swept, pnl, reset or prev is None, rec["action"], Jsonb(detail))).fetchone()
    log.info("btc bet advice %s: %s stake=%.2f p_yes=%.3f active=%.2f vault=%.2f", row["id"], rec["action"], rec["cost"],
             model["p_yes"], active, vault)
    return {"id": row["id"], "created_at": row["created_at"], **detail}


def check(s: Settings, *, sell_price: float, paid_price: float | None = None) -> dict:
    """Intra-day: with the market now paying `sell_price` per share you hold, sell or keep the latest recommended position?
    `paid_price` is what you actually paid per share; without it the recommendation's highest acceptable price is assumed."""
    last = latest_state()
    if last is None or last["action"] not in ("BUY_YES", "BUY_NO"):
        raise AdvisorError(409, "There is no open position: the latest recommendation was not a bet.")
    d = last["detail"]
    now = datetime.now(timezone.utc)
    settle_at = datetime.fromisoformat(d["inputs"]["settle_at"])
    if settle_at <= now:
        raise AdvisorError(409, "That position has already settled. Enter today's balance to start the next cycle.")
    hours = (settle_at - now).total_seconds() / 3600
    spot = float(_spot(s)["price_usd"])
    model = _model(s, spot, float(d["inputs"]["strike"]), hours)
    win = model["p_yes"] if last["action"] == "BUY_YES" else 1 - model["p_yes"]
    entry = paid_price or float(d["entry_price"])
    win = betting.blend(win, sell_price, track_record(s)["trust"])  # the current market price is information too
    out = betting.check_position(entry, sell_price, win, int(float(d["cost"]) / entry), params(s))
    return out | {"side": last["action"], "spot": spot, "hours_left": round(hours, 2), "strike": float(d["inputs"]["strike"])}


def overview(s: Settings, history: int = 14) -> dict:
    with get_pool().connection() as conn:
        rows = conn.execute("select id, created_at, balance, active, vault, peak, swept, pnl, is_reset, action, detail "
                            "from bitcoin_bets order by created_at desc limit %s", (history,)).fetchall()
    now = datetime.now(timezone.utc)
    nxt = next_settlement(s, now)
    p = params(s)
    last = rows[0] if rows else None
    open_position = bool(last and last["action"] in ("BUY_YES", "BUY_NO") and datetime.fromisoformat(last["detail"]["inputs"]["settle_at"]) > now)
    live = None
    quoted = bool(last and "up_x" in last["detail"]["inputs"])  # check-ins made before quotes were an input cannot be re-asked
    if quoted and (open_position or (last["action"] == "HOLD_CASH" and datetime.fromisoformat(last["detail"]["inputs"]["settle_at"]) > now)):
        try:  # the same question, re-asked at the current stored price (the page's pulse keeps that price fresh)
            d = last["detail"]
            with get_pool().connection() as conn:
                price = conn.execute("select price_usd from bitcoin_prices order by fetched_at desc limit 1").fetchone()
            hours = (datetime.fromisoformat(d["inputs"]["settle_at"]) - now).total_seconds() / 3600
            spot = float(price["price_usd"])
            m = _model(s, spot, float(d["inputs"]["strike"]), hours)
            rec, _ = _decide(s, m, float(d["inputs"]["up_x"]), float(d["inputs"]["down_x"]), float(last["active"]),
                             replace(p, min_trade=float(d["inputs"].get("min_bet", p.min_trade))), track_record(s))
            live = {"spot": spot, "p_yes": m["p_yes"], "action": rec["action"], "changed": rec["action"] != last["action"], "hours_left": round(hours, 2)}
        except Exception:  # noqa: BLE001 - the live line is a convenience; never break the panel over it
            log.exception("bet advisor: live re-evaluation failed")
    return {
        "live": live,
        "state": None if last is None else {k: float(last[k]) for k in ("balance", "active", "vault", "peak")} | {"updated_at": last["created_at"]},
        "last": None if last is None else {"id": last["id"], "created_at": last["created_at"], **last["detail"]},
        "open_position": open_position,
        "history": [{"id": r["id"], "created_at": r["created_at"], "balance": float(r["balance"]), "vault": float(r["vault"]),
                     "pnl": None if r["pnl"] is None else float(r["pnl"]), "action": r["action"], "shares": r["detail"].get("shares", 0),
                     "entry_price": r["detail"].get("entry_price"), "cost": r["detail"].get("cost", 0.0),
                     "strike": r["detail"]["inputs"]["strike"], "is_reset": r["is_reset"]} for r in rows],
        "record": track_record(s),
        "defaults": {"hours_to_settle": round((nxt - now).total_seconds() / 3600, 1), "settle_at": nxt,
                     "min_bet": float(last["detail"]["inputs"].get("min_bet", p.min_trade)) if last else p.min_trade,
                     "settle_label": f"{s.bitcoin_bet_settle_hour:02d}:00 {s.bitcoin_bet_timezone}"},
        "params": {"min_edge": p.min_edge, "kelly": p.kelly, "max_fraction": p.f_max, "vault_share": p.alpha, "take_profit": p.take,
                   "stop_loss": p.stop, "min_trade": p.min_trade, "fee": p.fee},
    }
