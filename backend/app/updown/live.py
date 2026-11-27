"""The always-on loop for the 5m / 15m BTC Up/Down markets (run by `python -m app.cli updown-live`, its own container).

Every UPDOWN_TICK_SECONDS:
    BTC spot (Coinbase) + both markets' order books (Polymarket CLOB)
        ──► features ──► champion model ──► P(Up) ──► snapshot row (the live record, and tomorrow's training data)
        ──► the alert rule (>= 90% on a side, edge over price + fee, time left) ──► Telegram ping (PAPER until proven)
Every minute: new 1-minute candles, and the results of windows that just ended (which settles bets you placed).
Every morning: the daily message (app/updown/daily.py).

`backfill` downloads finished windows (result + minute prices) so the model can be trained before the loop has run.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
from psycopg.types.json import Jsonb

from app import polymarket as pm
from app.bitcoin import advisor, bankroll, betting
from app.bitcoin import candles as cdl
from app.config import Settings, get_settings
from app.db import get_pool
from app.updown import features as ft
from app.updown import model as md

log = logging.getLogger(__name__)
ET = "America/New_York"
K_FROM_TWAP = True  # price to beat from the live feed: the 60-second average before the start (Chainlink TWAP-60 stream)


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def label(w: dict | pm.UpDown) -> str:
    """'BTC 15m, 8:15-8:30 AM ET'."""
    from zoneinfo import ZoneInfo
    start = w.start if isinstance(w, pm.UpDown) else w["start_at"]
    end = w.end if isinstance(w, pm.UpDown) else w["end_at"]
    minutes = w.minutes if isinstance(w, pm.UpDown) else w["minutes"]
    a, b = start.astimezone(ZoneInfo(ET)), end.astimezone(ZoneInfo(ET))
    return f"BTC {minutes}m, {a:%-I:%M}-{b:%-I:%M %p} ET"


# ----------------------------------------------------------------------------------------------- windows
def upsert_window(w: pm.UpDown, price_to_beat: float | None = None, basis: float | None = None) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            """insert into updown_windows (slug, minutes, start_at, end_at, up_token, down_token, fee_rate, fee_exponent, min_order_size,
                                           price_to_beat, final_price, basis, outcome, resolved_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, case when %s::text is null then null else now() end)
               on conflict (slug) do update set price_to_beat = coalesce(excluded.price_to_beat, updown_windows.price_to_beat),
                    final_price = coalesce(excluded.final_price, updown_windows.final_price),
                    basis = coalesce(updown_windows.basis, excluded.basis),
                    outcome = coalesce(updown_windows.outcome, excluded.outcome),
                    resolved_at = coalesce(updown_windows.resolved_at, excluded.resolved_at)""",
            (w.slug, w.minutes, w.start, w.end, w.up_token, w.down_token, w.fee_rate, w.fee_exponent, w.min_order_size,
             price_to_beat or w.price_to_beat, w.final_price, basis, w.outcome, w.outcome))


def coinbase_at(series: cdl.Series, ts: float) -> float | None:
    """Coinbase BTC at exactly `ts` (a minute boundary): the close of the minute that ends there."""
    i = series.last_closed(ts)
    return float(series.close[i]) if i >= 0 and series.t[i] == int(ts) - 60 else None


def basis_for(series: cdl.Series, start: float, price_to_beat: float | None) -> float | None:
    """Chainlink minus Coinbase at the window start. Adding it to the live Coinbase price estimates the Chainlink price
    the market settles on; without it a $30 gap between exchanges would decide 5-minute windows for the wrong reason."""
    c = coinbase_at(series, start)
    return round(price_to_beat - c, 2) if price_to_beat and c else None


# ----------------------------------------------------------------------------------------------- backfill
def backfill(settings: Settings | None = None, *, hours: float = 24, http: httpx.Client | None = None, pause: float = 0.12) -> dict:
    """Download finished windows of the last `hours`: each window's result and its minute-by-minute Up price."""
    s = settings or get_settings()
    own = http is None
    http = http or pm.client(s)
    try:
        end = pm.window_start(time.time(), 15)
        start = end - int(hours * 3600)
        cdl.sync(http, start - 1441 * 60, end)
        series = cdl.load(_utc(start - 1441 * 60), _utc(end))
        with get_pool().connection() as conn:
            have = {r["slug"] for r in conn.execute("select slug from updown_windows where outcome is not null and start_at >= %s", (_utc(start),))}
        slugs = [pm.updown_slug(m, t) for m in pm.DURATIONS for t in range(pm.window_start(start, m), end, m * 60)]
        todo = [x for x in slugs if x not in have]
        stats = {"windows_seen": 0, "windows_saved": 0, "rows": 0, "skipped_unresolved": 0}
        for i in range(0, len(todo), 20):
            for ev in pm.get_events(http, todo[i:i + 20]):
                stats["windows_seen"] += 1
                try:
                    w = pm.parse_updown(ev)
                except (ValueError, KeyError, IndexError) as exc:
                    log.warning("skipping %s: %r", ev.get("slug"), exc)
                    continue
                if w.outcome is None:
                    stats["skipped_unresolved"] += 1
                    continue
                if w.price_to_beat is None:
                    stats["skipped_unresolved"] += 1
                    continue
                basis = basis_for(series, w.start.timestamp(), w.price_to_beat)
                upsert_window(w, basis=basis)
                hist = pm.price_history(http, w.up_token, int(w.start.timestamp()), int(w.end.timestamp()))
                rows = []
                for t, p in hist:
                    btc = series.close_at(t)
                    if btc and basis is not None and w.start.timestamp() <= t < w.end.timestamp() and 0 <= p <= 1:
                        rows.append((w.slug, _utc(t), round(btc + basis, 2), p))  # Coinbase + gap ~ Chainlink
                with get_pool().connection() as conn, conn.cursor() as cur:
                    cur.executemany("""insert into updown_snapshots (slug, at, btc, up_price, source) values (%s,%s,%s,%s,'backfill')
                                       on conflict (slug, at) do nothing""", rows)
                stats["windows_saved"] += 1
                stats["rows"] += len(rows)
                time.sleep(pause)
        log.info("updown backfill (%sh): %s", hours, stats)
        return stats
    finally:
        if own:
            http.close()


# ----------------------------------------------------------------------------------------------- the live loop
class Live:
    def __init__(self, settings: Settings, http: httpx.Client, notify=None, say=None, feed=None) -> None:
        self.s, self.http = settings, http
        self.feed = feed      # app.bitcoin.chainlink.Feed: the live Chainlink price (None = Coinbase + basis only)
        self.notify = notify  # callable(text, signal_payload): a signal ping (the assistant remembers it for "in")
        self.say = say        # callable(text): any other message (results of windows you bet on)
        self.windows: dict[str, dict] = {}
        self.series: cdl.Series | None = None
        self.regime: ft.Regime | None = None
        self.champ: dict | None = None
        self._t_candles = self._t_model = self._t_resolve = self._t_regime = 0.0

    # -- slow-changing inputs
    def refresh(self, now: float) -> None:
        if now - self._t_candles >= 60:
            latest = cdl.latest_time()
            since = max(latest.timestamp() - 120, now - 2 * 86400) if latest else now - 2 * 86400
            cdl.sync(self.http, int(since), int(now))
            self.series = cdl.load(_utc(now - 1441 * 60 - 120), _utc(now + 60))
            self._t_candles = now
        if now - self._t_model >= 300:
            self.champ = md.champion()
            self._t_model = now
        if now - self._t_regime >= 3600:
            self.regime = md.load_regime()
            self._t_regime = now

    def window(self, minutes: int, now: float) -> dict | None:
        start = pm.window_start(now, minutes)
        slug = pm.updown_slug(minutes, start)
        w = self.windows.get(slug)
        if w is None:
            u = pm.get_updown(self.http, minutes, start)
            if u is None:
                return None
            upsert_window(u, None)
            w = {"slug": slug, "minutes": minutes, "start": start, "end": u.end.timestamp(), "u": u, "k": None, "k_source": None,
                 "basis": None, "tried": 0.0}
            self.windows = {k: v for k, v in self.windows.items() if v["end"] > now - 3600} | {slug: w}
        if w["k"] is None and now - w["tried"] >= 15:
            # Official numbers first (published only after a window ends, so usually not yet), then the live Chainlink
            # feed's reading of the window start, which is what the market's page shows as "Price To Beat".
            w["tried"] = now
            prev = pm.get_updown(self.http, minutes, start - minutes * 60)
            k, source = (w["u"].price_to_beat, "official") if w["u"].price_to_beat else \
                        (prev.final_price, "official") if prev and prev.final_price else \
                        (self.feed_price_to_beat(start), "chainlink")
            if k and self.series is not None:
                w["k"], w["k_source"], w["basis"] = round(k, 2), source, basis_for(self.series, start, k)
                with get_pool().connection() as conn:
                    conn.execute("update updown_windows set price_to_beat = %s, basis = coalesce(basis, %s) where slug = %s", (k, w["basis"], slug))
        return w

    def feed_price_to_beat(self, start: float) -> float | None:
        if not self.feed:
            return None
        return self.feed.twap(start, 60) if K_FROM_TWAP else self.feed.at(start)

    def tick(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        self.refresh(now)
        live = self.feed.latest(now) if self.feed else None
        spot = live if live is not None else cdl.spot(self.http)
        out = []
        for minutes in pm.DURATIONS:
            try:
                w = self.window(minutes, now)
                if w:
                    # Chainlink when the feed is live; otherwise Coinbase corrected by this window's measured gap
                    price = spot if live is not None else (spot + w["basis"] if w["basis"] is not None else None)
                    out.append(self.observe(w, price, now, "chainlink" if live is not None else "coinbase+basis"))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("updown %sm tick failed: %r", minutes, exc)
        if now - self._t_resolve >= 60:
            self.resolve_due(now)
            self._t_resolve = now
        return out

    def observe(self, w: dict, price: float | None, now: float, price_source: str = "chainlink") -> dict:
        """`price`: BTC on the settlement basis (Chainlink, or Coinbase + basis); None until the price to beat is known."""
        u: pm.UpDown = w["u"]
        up_bid, up_ask = pm.get_book(self.http, u.up_token)
        down_bid, down_ask = pm.get_book(self.http, u.down_token)
        up_price = (up_bid + up_ask) / 2 if up_bid is not None and up_ask is not None else (u.up_price or 0.5)
        snap = {"slug": w["slug"], "minutes": w["minutes"], "btc": price and round(price, 2), "price_source": price_source,
                "price_to_beat": w["k"], "k_source": w["k_source"], "up_price": round(up_price, 4), "up_ask": up_ask, "down_ask": down_ask,
                "secs_left": round(w["end"] - now), "p_model": None, "signal": None}
        if price is None:
            return snap  # nothing comparable to the price to beat yet: no snapshot, no prediction
        snap["btc_index"] = snap["btc"]
        if self.champ and w["k"] and self.series is not None and self.regime is not None:
            x, _ = ft.build(at=now, start=w["start"], end=w["end"], minutes=w["minutes"], price=price, price_to_beat=w["k"],
                            p_market=up_price, candles=self.series, regime=self.regime)
            snap["p_model"] = round(float(md.predict(self.champ["params"], x)[0]), 4)
        with get_pool().connection() as conn:
            conn.execute("""insert into updown_snapshots (slug, at, btc, up_price, up_ask, down_ask, source, p_model, model_version)
                            values (%s,%s,%s,%s,%s,%s,'live',%s,%s) on conflict (slug, at) do nothing""",
                         (w["slug"], _utc(now), snap["btc"], up_price, up_ask, down_ask, snap["p_model"], self.champ["version"] if self.champ else None))
        if snap["p_model"] is not None:
            up_cost = md.side_cost(up_ask, u.fee_rate, u.fee_exponent)
            down_cost = md.side_cost(down_ask, u.fee_rate, u.fee_exponent)
            sig = md.signal(snap["p_model"], up_cost, down_cost, w["end"] - now, self.s)
            if sig:
                snap["signal"] = self.fire(w, snap, sig, up_cost, down_cost)
        return snap

    def fire(self, w: dict, snap: dict, sig: tuple[str, float, float], up_cost: float | None, down_cost: float | None) -> dict | None:
        side, p_side, cost = sig
        proven = bool(self.champ["metrics"].get("proven"))
        st = bankroll.latest()
        active = st["active"] if st else 0.0
        params = replace(advisor.params(self.s), min_trade=self.s.updown_min_bet, min_edge=self.s.updown_min_edge)
        rec = betting.advise(snap["p_model"], 1 / up_cost if up_cost else 1.0001, 1 / down_cost if down_cost else 1.0001, active, params)
        stake = rec["cost"] if rec["action"] in ("BUY_YES", "BUY_NO") else 0.0
        detail = {"label": label(w["u"]), "btc": snap.get("btc_index", snap["btc"]), "price_to_beat": snap["price_to_beat"], "secs_left": snap["secs_left"],
                  "multiplier": round(1 / cost, 3), "edge": round(p_side - cost, 4), "active": active, "advice": rec["reason"],
                  "min_order_usd": round(w["u"].min_order_size * cost, 2), "model": self.champ["version"]}
        with get_pool().connection() as conn:
            row = conn.execute("""insert into updown_signals (slug, side, p_model, price, stake, proven, detail) values (%s,%s,%s,%s,%s,%s,%s)
                                  on conflict (slug, side) do nothing returning id""",
                               (w["slug"], side, round(p_side, 4), round(cost, 4), stake, proven, Jsonb(detail))).fetchone()
        if row is None:
            return None  # already signalled this window and side
        payload = {"id": row["id"], "slug": w["slug"], "side": side, "p": p_side, "cost": cost, "stake": stake, "proven": proven,
                   "end": w["end"], **detail}
        if self.notify:
            try:
                self.notify(signal_text(payload), payload)
                with get_pool().connection() as conn:
                    conn.execute("update updown_signals set notified = true where id = %s", (row["id"],))
            except Exception:  # noqa: BLE001 - a failed ping must never stop the loop
                log.exception("could not send the signal")
        return payload

    def resolve_due(self, now: float) -> list[dict]:
        with get_pool().connection() as conn:
            due = conn.execute("""select slug from updown_windows where outcome is null and end_at < %s and end_at > %s
                                  order by end_at limit 40""", (_utc(now - 20), _utc(now - 3 * 86400))).fetchall()
        done = []
        if not due:
            return done
        for ev in pm.get_events(self.http, [r["slug"] for r in due]):
            try:
                u = pm.parse_updown(ev)
            except (ValueError, KeyError, IndexError):
                continue
            if u.outcome:
                with get_pool().connection() as conn:
                    conn.execute("""update updown_windows set outcome = %s, resolved_at = now(), final_price = %s,
                                           price_to_beat = coalesce(%s, price_to_beat) where slug = %s and outcome is null""",
                                 (u.outcome, u.final_price, u.price_to_beat, u.slug))
                done.append({"slug": u.slug, "outcome": u.outcome, "label": label(u)})
                if self.say:
                    try:
                        from app import assistant
                        assistant.on_resolved(self.s, u.slug, u.outcome, label(u), self.say)
                    except Exception:  # noqa: BLE001
                        log.exception("could not settle bets for %s", u.slug)
        return done


def signal_text(p: dict) -> str:
    side = p["side"].upper()
    head = f"BET {side}" if p["proven"] else f"PAPER SIGNAL {side} (model not proven yet: practice only, no real money)"
    gap = p["btc"] - p["price_to_beat"]
    mins, secs = divmod(int(p["secs_left"]), 60)
    lines = [f"{head}: {p['label']}",
             f"Model: {p['p'] * 100:.0f}% {side}. Price {p['cost']:.2f} incl. fee, pays {p['multiplier']:.2f}x, edge +{p['edge'] * 100:.0f} pts.",
             f"BTC ${p['btc']:,.0f} vs price to beat ${p['price_to_beat']:,.0f} ({gap:+,.0f}). {mins}:{secs:02d} left."]
    if p["proven"]:
        lines.append(f"Stake: ${p['stake']:.2f} of your ${p['active']:.2f} active." if p["stake"] else f"No stake: {p['advice']}")
        if p["stake"] and p["min_order_usd"] > p["stake"]:
            lines.append(f"Use a market order of ${p['stake']:.2f} (a limit order needs at least ${p['min_order_usd']:.2f}).")
        lines.append('Reply "in" when placed (or "in 2.50" for another amount), or "skip".')
    return "\n".join(lines)


def run_forever(settings: Settings | None = None, stop: threading.Event | None = None) -> None:
    """The service: the tick loop here, the Telegram assistant (if configured) in a second thread, the morning message."""
    from app import assistant
    from app.updown import daily

    s = settings or get_settings()
    stop = stop or threading.Event()
    http = pm.client(s, timeout=8)  # a stuck request must not freeze a 10-second loop for long
    bot = assistant.from_settings(s, http)
    if bot:
        threading.Thread(target=assistant.poll_forever, args=(s, bot, stop), name="telegram", daemon=True).start()
        bot.notify("MLE-Sprint assistant is online. I will ping you about BTC 5m/15m signals and send the daily plan. Send /help.")
    from app.bitcoin.chainlink import Feed

    live = Live(s, http, notify=bot.notify_signal if bot else None, say=bot.notify if bot else None, feed=Feed().start())
    log.info("updown live loop started (tick %ss, telegram %s)", s.updown_tick_seconds, "on" if bot else "off")
    while not stop.is_set():
        t0 = time.time()
        try:
            live.tick(t0)
            daily.maybe_send(s, bot, t0)
        except Exception:  # noqa: BLE001 - keep running through network blips and exchange hiccups
            log.exception("updown tick failed")
        stop.wait(max(1.0, s.updown_tick_seconds - (time.time() - t0)))


def prune(days: int = 60) -> int:
    with get_pool().connection() as conn:
        return conn.execute("delete from updown_windows where end_at < now() - make_interval(days => %s)", (days,)).rowcount


def status(settings: Settings | None = None) -> dict:
    """What the website shows: the current windows, the model, recent signals and the paper/real track record."""
    s = settings or get_settings()
    with get_pool().connection() as conn:
        windows = conn.execute(
            """select distinct on (w.minutes) w.slug, w.minutes, w.start_at, w.end_at, w.price_to_beat, w.basis, s.at, s.btc,
                      s.btc as btc_index, s.up_price, s.up_ask,
                      s.down_ask, s.p_model
               from updown_windows w join updown_snapshots s using (slug)
               where s.source = 'live' and w.end_at > now() - interval '1 minute' order by w.minutes, s.at desc""").fetchall()
        signals = conn.execute(
            """select g.id, g.created_at, g.slug, g.side, g.p_model, g.price, g.stake, g.proven, g.detail, w.outcome, w.minutes
               from updown_signals g join updown_windows w using (slug) order by g.created_at desc limit 20""").fetchall()
        rec = conn.execute(
            """select count(*) filter (where w.outcome is not null) as settled,
                      count(*) filter (where w.outcome = g.side) as wins,
                      coalesce(sum(case when w.outcome is null then 0 when w.outcome = g.side then 1 / g.price - 1 else -1 end), 0) as pnl
               from updown_signals g join updown_windows w using (slug) where g.created_at > now() - interval '30 days'""").fetchone()
        counts = conn.execute("""select count(*) filter (where outcome is not null) as resolved, count(*) as total,
                                        max(created_at) as newest from updown_windows""").fetchone()
        last_live = conn.execute("select max(at) as at from updown_snapshots where source = 'live'").fetchone()["at"]
        picks = conn.execute("select value, updated_at from assistant_state where key = 'daily_picks'").fetchone()
    ch = md.champion()
    return {
        "running": bool(last_live and last_live > datetime.now(timezone.utc) - timedelta(seconds=max(60, 4 * s.updown_tick_seconds))),
        "last_tick": last_live, "telegram": bool(s.telegram_bot_token and s.telegram_chat_id),
        "windows": [dict(w) | {"label": label(w)} for w in windows],
        "model": None if ch is None else {"version": ch["version"], "kind": ch["kind"], "created_at": ch["created_at"],
                                           "proven": bool(ch["metrics"].get("proven")), "summary": md.describe(ch["metrics"]),
                                           "metrics": ch["metrics"], "review": ch["review"]},
        "data": {"windows_resolved": counts["resolved"], "windows_total": counts["total"]},
        "signals": [dict(r) for r in signals],
        "record": {"settled": rec["settled"], "wins": rec["wins"], "pnl_per_dollar": round(float(rec["pnl"]), 3)},
        "daily": None if picks is None else picks["value"] | {"updated_at": picks["updated_at"]},
        "rule": {"prob": s.updown_alert_prob, "min_edge": s.updown_min_edge, "min_secs": s.updown_alert_min_secs},
    }
