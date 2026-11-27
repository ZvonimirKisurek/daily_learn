"""The daily plan: one message each morning so you never have to watch the screen to know whether today is a betting day.

    Polymarket's "Bitcoin above ___ on <date>" ladder (11 target prices, settles at noon ET on Binance's 1-minute close)
        x  the 1-day forecast (Claude reads the news + technicals; calibrated by its own track record)
        ──► P(above) for every target price (betting.prob_above) ──► edge per YES / NO ──► the best bet, sized by Kelly
    + how the 5m/15m model is doing, your bankroll, and a request for your balance when it is stale.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app import polymarket as pm
from app.bitcoin import advisor, bankroll, betting
from app.bitcoin import candles as cdl
from app.config import Settings
from app.db import get_pool
from app.updown import model as md

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
_lock = threading.Lock()


def target_date(now: datetime) -> datetime.date:
    """Today's ladder until noon ET, then tomorrow's."""
    et = now.astimezone(ET)
    return et.date() if et.hour < 12 else et.date() + timedelta(days=1)


def ensure_forecast(s: Settings, max_age_hours: float = 12) -> None:
    """The daily plan needs a fresh 1-day forecast; make one with Claude if the last is too old."""
    with get_pool().connection() as conn:
        r = conn.execute("select max(created_at) as at from bitcoin_predictions").fetchone()
    if r["at"] is None or r["at"] < datetime.now(timezone.utc) - timedelta(hours=max_age_hours):
        from app.bitcoin.predictor import generate_prediction
        generate_prediction(trigger="beat", settings=s)


def picks(s: Settings, http, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    d = target_date(now)
    events = pm.get_events(http, [pm.daily_slug(d)])
    if not events:
        return {"date": d.isoformat(), "error": "Polymarket has no 'Bitcoin above' market for this date yet."}
    ladder = pm.parse_daily(events[0])
    if not ladder["rows"] or ladder["settle_at"] is None:
        return {"date": d.isoformat(), "error": "No open target prices on today's ladder."}
    spot = cdl.spot(http)
    hours = max((ladder["settle_at"] - now).total_seconds() / 3600, 0.05)
    try:
        m = advisor._model(s, spot, ladder["rows"][0]["strike"], hours)
    except advisor.AdvisorError as exc:
        return {"date": d.isoformat(), "error": exc.detail}
    st = bankroll.latest()
    active = st["active"] if st else 0.0
    params = replace(advisor.params(s), min_trade=s.updown_min_bet)
    rows, best = [], None
    for r in ladder["rows"]:
        p_yes = betting.prob_above(spot, r["strike"], m["sigma_daily"], hours, m["calibrated_up_1d"])
        yes_cost = md.side_cost(r["yes_ask"], r["fee_rate"], r["fee_exponent"])
        no_cost = md.side_cost(1 - r["yes_bid"], r["fee_rate"], r["fee_exponent"])
        row = {"strike": r["strike"], "p_yes": round(p_yes, 4), "yes_cost": yes_cost and round(yes_cost, 4), "no_cost": no_cost and round(no_cost, 4),
               "edge_yes": None if yes_cost is None else round(p_yes - yes_cost, 4), "edge_no": None if no_cost is None else round(1 - p_yes - no_cost, 4)}
        rows.append(row)
        for side, edge, cost in (("yes", row["edge_yes"], yes_cost), ("no", row["edge_no"], no_cost)):
            if edge is not None and edge >= params.min_edge and (best is None or edge > best["edge"]):
                best = {"strike": r["strike"], "side": side, "edge": edge, "cost": round(cost, 4), "question": r["question"],
                        "p_win": round(p_yes if side == "yes" else 1 - p_yes, 4)}
    if best:
        p_up = best["p_win"] if best["side"] == "yes" else 1 - best["p_win"]
        yes_cost, no_cost = (best["cost"], None) if best["side"] == "yes" else (None, best["cost"])
        rec = betting.advise(p_up, 1 / yes_cost if yes_cost else 1.0001, 1 / no_cost if no_cost else 1.0001, active, params)
        best |= {"stake": rec["cost"] if rec["action"] in ("BUY_YES", "BUY_NO") else 0.0, "advice": rec["reason"],
                 "multiplier": round(1 / best["cost"], 3)}
    return {"date": d.isoformat(), "settle_at": ladder["settle_at"].isoformat(), "title": ladder["title"], "spot": spot, "hours": round(hours, 1),
            "forecast": {"stated_up_1d": m["stated_up_1d"], "calibrated_up_1d": m["calibrated_up_1d"], "note": m["calibration_note"],
                         "age_hours": m["forecast_age_hours"]},
            "rows": rows, "best": best, "active": active}


def text(s: Settings, p: dict) -> str:
    """The morning message."""
    lines = [f"Good morning. Daily plan for {p['date']}"]
    if p.get("error"):
        lines.append(f"Daily market: {p['error']}")
    else:
        f = p["forecast"]
        lean = "UP" if f["calibrated_up_1d"] > 0.5 else "DOWN" if f["calibrated_up_1d"] < 0.5 else "no lean"
        lines.append(f"BTC ${p['spot']:,.0f}. Forecast for the next day: {lean} ({f['calibrated_up_1d'] * 100:.0f}% Up after calibration; {f['note']}).")
        b = p["best"]
        if b:
            lines.append(f"Best daily bet: {b['side'].upper()} on 'above ${b['strike']:,.0f}' at {b['cost']:.2f} (pays {b['multiplier']:.2f}x). "
                         f"Model {b['p_win'] * 100:.0f}% vs price {b['cost'] * 100:.0f}%: edge +{b['edge'] * 100:.0f} pts. "
                         + (f"Stake ${b['stake']:.2f}." if b["stake"] else f"No stake: {b['advice']}"))
        else:
            lines.append("No daily bet today: every target price is priced at or beyond what the forecast thinks it is worth.")
        lines.append(f"Settles {p['hours']:.0f} h from now (noon ET).")
    ch = md.champion()
    if ch:
        lines.append("5m/15m model: " + md.describe(ch["metrics"]))
    else:
        lines.append("5m/15m model: not trained yet (it needs resolved windows; training runs every 3 hours).")
    st = bankroll.latest()
    if st is None or st["created_at"] < datetime.now(timezone.utc) - timedelta(hours=20):
        lines.append("What is your balance right now? Reply with just the number, for example 12.40.")
    else:
        lines.append(bankroll.explain(st | {"swept": 0, "pnl": None}, s.bitcoin_bet_vault_share))
    return "\n".join(lines)


def _state(key: str):
    with get_pool().connection() as conn:
        r = conn.execute("select value from assistant_state where key = %s", (key,)).fetchone()
    return r["value"] if r else None


def _set(key: str, value) -> None:
    with get_pool().connection() as conn:
        conn.execute("""insert into assistant_state (key, value) values (%s, %s)
                        on conflict (key) do update set value = excluded.value, updated_at = now()""", (key, Jsonb(value)))


def refresh(s: Settings, http, now: datetime | None = None, forecast: bool = False) -> dict:
    if forecast:
        try:
            ensure_forecast(s)
        except Exception:  # noqa: BLE001 - an older forecast is still better than no plan
            log.exception("could not refresh the forecast for the daily plan")
    p = picks(s, http, now)
    _set("daily_picks", p)
    return p


def maybe_send(s: Settings, bot, ts: float) -> None:
    """Called every tick: refresh the website's daily picks hourly, and send the morning message once a day. The slow
    part (a fresh Claude forecast) runs in a background thread so the 10-second loop never stalls."""
    now = datetime.fromtimestamp(ts, timezone.utc)
    local = now.astimezone(ZoneInfo(s.bitcoin_bet_timezone))
    due_daily = local.hour >= s.updown_daily_hour and _state("daily_sent") != local.date().isoformat()
    hourly = _state("daily_refreshed_hour") != now.strftime("%Y-%m-%dT%H")
    if not (due_daily or hourly) or not _lock.acquire(blocking=False):
        return
    _set("daily_refreshed_hour", now.strftime("%Y-%m-%dT%H"))
    if due_daily:
        _set("daily_sent", local.date().isoformat())

    def work() -> None:
        try:
            with pm.client(s) as http:
                p = refresh(s, http, forecast=due_daily)
            if due_daily and bot:
                bot.notify(text(s, p))
        except Exception:  # noqa: BLE001
            log.exception("daily plan failed")
        finally:
            _lock.release()

    threading.Thread(target=work, name="daily-plan", daemon=True).start()
