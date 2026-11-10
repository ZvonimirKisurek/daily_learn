"""The bet advisor's I/O: live price, latest forecast, track record and bankroll state in; a stored recommendation out.

Synchronous on purpose (the API runs it in a worker thread): it makes one outbound price request and a few small queries,
and reuses the same sync helpers as the forecast job. All arithmetic lives in betting.py.
"""
from __future__ import annotations

import logging
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


def latest_state() -> dict | None:
    with get_pool().connection() as conn:
        return conn.execute("select * from bitcoin_bets order by created_at desc limit 1").fetchone()


def advise(s: Settings, *, balance: float, strike: float, yes_ask: float, yes_bid: float | None, hours_to_settle: float | None,
           reset: bool = False) -> dict:
    """The daily check-in: settle the bankroll against the reported balance, then size today's bet."""
    yes_bid = yes_ask if yes_bid is None else yes_bid
    if yes_bid > yes_ask:
        raise AdvisorError(422, "The YES bid (sell price) cannot be higher than the YES ask (buy price).")
    now = datetime.now(timezone.utc)
    settle_at = now + timedelta(hours=hours_to_settle) if hours_to_settle else next_settlement(s, now)
    hours = (settle_at - now).total_seconds() / 3600
    p = params(s)

    prev = None if reset else latest_state()
    if prev is None:  # first check-in (or a reset): everything is active, nothing vaulted, the peak is where you start
        active, vault, peak, swept, pnl = round(balance, 2), 0.0, round(balance, 2), 0.0, None
    else:
        active, vault, peak, swept = betting.settle(balance, float(prev["vault"]), float(prev["peak"]), p.alpha)
        pnl = round(balance - float(prev["balance"]), 2)

    price = _spot(s)
    spot = float(price["price_usd"])
    model = _model(s, spot, strike, hours)
    rec = betting.advise(model["p_yes"], yes_ask, yes_bid, active, p)

    warnings = []
    if model["forecast_age_hours"] > STALE_FORECAST_HOURS:
        warnings.append(f"The forecast is {model['forecast_age_hours']:.0f} hours old. Press Refresh on the Bitcoin page and ask again for a current view.")
    if (now - price["fetched_at"]).total_seconds() > 900:
        warnings.append("The live price could not be fetched; the last stored price was used.")
    if max(rec["edge_yes"], rec["edge_no"]) >= betting.SUSPICIOUS_EDGE:
        warnings.append("The model and the market disagree by a lot. That is more often a typo or something the model cannot see than "
                        "easy money: double-check the target price and the YES price before betting.")
    if model["shrink"] == 0:
        warnings.append("The forecast has not shown a directional edge recently, so any bet here rests only on volatility vs the strike.")
    detail = {
        **rec,
        "inputs": {"balance": round(balance, 2), "strike": strike, "yes_ask": yes_ask, "yes_bid": yes_bid, "hours_to_settle": round(hours, 2),
                   "settle_at": settle_at.isoformat()},
        "market": {"spot": spot, "spot_at": price["fetched_at"].isoformat()},
        "model": {k: (v.isoformat() if isinstance(v, datetime) else str(v) if k == "prediction_id" else v) for k, v in model.items()},
        "bankroll": {"balance": round(balance, 2), "active": active, "vault": vault, "peak": peak, "swept": swept, "pnl": pnl},
        "warnings": warnings,
    }
    with get_pool().connection() as conn:
        row = conn.execute(
            """insert into bitcoin_bets (prediction_id, balance, active, vault, peak, swept, pnl, is_reset, action, detail)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id, created_at""",
            (model["prediction_id"], round(balance, 2), active, vault, peak, swept, pnl, reset or prev is None, rec["action"], Jsonb(detail))).fetchone()
    log.info("btc bet advice %s: %s shares=%s p_yes=%.3f ask=%.2f active=%.2f vault=%.2f", row["id"], rec["action"], rec["shares"],
             model["p_yes"], yes_ask, active, vault)
    return {"id": row["id"], "created_at": row["created_at"], **detail}


def check(s: Settings, *, sell_price: float) -> dict:
    """Intra-day: with the market now paying `sell_price` per share you hold, sell or keep the latest recommended position?"""
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
    out = betting.check_position(float(d["entry_price"]), sell_price, win, int(d["shares"]), params(s))
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
    if open_position or (last and last["action"] == "HOLD_CASH" and datetime.fromisoformat(last["detail"]["inputs"]["settle_at"]) > now):
        try:  # the same question, re-asked at the current stored price (the page's pulse keeps that price fresh)
            d = last["detail"]
            with get_pool().connection() as conn:
                price = conn.execute("select price_usd from bitcoin_prices order by fetched_at desc limit 1").fetchone()
            hours = (datetime.fromisoformat(d["inputs"]["settle_at"]) - now).total_seconds() / 3600
            spot = float(price["price_usd"])
            m = _model(s, spot, float(d["inputs"]["strike"]), hours)
            rec = betting.advise(m["p_yes"], float(d["inputs"]["yes_ask"]), float(d["inputs"]["yes_bid"]), float(last["active"]), p)
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
        "defaults": {"hours_to_settle": round((nxt - now).total_seconds() / 3600, 1), "settle_at": nxt,
                     "settle_label": f"{s.bitcoin_bet_settle_hour:02d}:00 {s.bitcoin_bet_timezone}"},
        "params": {"min_edge": p.min_edge, "kelly": p.kelly, "max_fraction": p.f_max, "vault_share": p.alpha, "take_profit": p.take,
                   "stop_loss": p.stop, "min_trade": p.min_trade, "fee": p.fee},
    }
