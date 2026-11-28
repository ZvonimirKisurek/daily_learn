"""The short-term Up/Down system: Polymarket parsing (against real responses), features, training, the alert rule, the
live loop, the Telegram assistant's conversation, and the vault rule on the shared bankroll."""
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import psycopg
import pytest

from app import polymarket as pm
from app.bitcoin.candles import Series
from app.config import Settings
from app.updown import features as ft
from app.updown import model as md
from tests.test_bitcoin import btc_db  # noqa: F401  (a fixture: fake price, news and candles for the forecast)

DATA = Path(__file__).parent / "data" / "polymarket"
S = Settings(updown_alert_prob=0.9, updown_min_edge=0.03, updown_alert_min_secs=45, updown_trust_min_bets=3)


def load(name):
    return json.loads((DATA / name).read_text())


# ------------------------------------------------------------------ Polymarket, against real (trimmed) responses
def test_updown_markets_parse_including_fees_and_results():
    open15 = pm.parse_updown(load("updown_15m_open.json")[0])
    assert open15.minutes == 15 and open15.slug == "btc-updown-15m-1795785300" and not open15.closed and open15.outcome is None
    assert open15.start == datetime(2026, 11, 27, 13, 15, tzinfo=timezone.utc) and (open15.end - open15.start).seconds == 900
    assert len(open15.up_token) > 40 and open15.up_token != open15.down_token
    assert open15.fee_rate == 0.05 and open15.fee_exponent == 1 and open15.min_order_size == 5
    done = pm.parse_updown(load("updown_15m_resolved.json")[0])
    assert done.closed and done.outcome == "down"                       # outcomePrices ["0", "1"]
    assert done.price_to_beat == pytest.approx(61573.50, abs=0.01) and done.final_price < done.price_to_beat   # Chainlink, official
    assert open15.price_to_beat is None                                 # published only with the result
    assert pm.parse_updown(load("updown_5m_open.json")[0]).minutes == 5
    assert pm.outcome_of(["1", "0"], True) == "up" and pm.outcome_of(["0.6", "0.4"], True) is None and pm.outcome_of(["1", "0"], False) is None


def test_slugs_windows_books_history_and_fees():
    assert pm.window_start(1795785872, 15) == 1795785300 and pm.window_start(1795785872, 5) == 1795785600
    assert pm.updown_slug(5, 1795785600) == "btc-updown-5m-1795785600"
    assert pm.daily_slug(date(2026, 11, 27)) == "bitcoin-above-on-november-27-2026"
    bid, ask = pm.best_prices(load("book.json"))                       # bids ascending, asks descending in the raw book
    assert bid == 0.18 and ask == 0.19
    assert pm.best_prices({"bids": [], "asks": []}) == (None, None)
    assert pm.fee_per_share(0.5, 0.05, 1) == pytest.approx(0.0125) and pm.fee_per_share(0.95, 0.05, 1) == pytest.approx(0.002375)
    hist = load("prices_history.json")["history"]
    assert len(hist) > 10 and {"t", "p"} <= set(hist[0])


def test_daily_ladder_parses_strikes_prices_and_settlement():
    d = pm.parse_daily(load("daily_above.json")[0])
    assert d["settle_at"] == datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)      # noon ET
    strikes = [r["strike"] for r in d["rows"]]
    assert strikes == sorted(strikes) and 60_000 in strikes
    r = next(r for r in d["rows"] if r["strike"] == 60_000)
    assert 0 < r["yes_bid"] <= r["yes_ask"] < 1 and r["fee_rate"] == 0.01


# ------------------------------------------------------------------ features
def series(start: int, closes) -> Series:
    return Series(np.arange(start, start + 60 * len(closes), 60, dtype=np.int64), np.array(closes, dtype=float))


def test_candles_never_peek_at_an_unfinished_minute():
    s = series(1000 * 60, [100.0, 101.0, 102.0])
    assert s.close_at(1000 * 60 + 59) is None                  # the first minute is not over yet
    assert s.close_at(1001 * 60) == 100.0 and s.close_at(1002 * 60 + 30) == 101.0


def test_z_grows_as_time_runs_out_and_the_analytic_probability_follows():
    s = series(0, 60_000 * np.exp(np.cumsum(np.full(200, 0.0))) + np.tile([0, 30], 100))
    reg = ft.Regime([], [], [])
    kw = dict(start=150 * 60, end=165 * 60, minutes=15, price=60_100, price_to_beat=60_000, p_market=0.6, candles=s, regime=reg)
    early, p_early = ft.build(at=151 * 60, **kw)
    late, p_late = ft.build(at=165 * 60 - 20, **kw)
    assert early.shape == (len(ft.FEATURES),) and late[0] > early[0] > 0 and 0.5 < p_early < p_late
    assert late[ft.FEATURES.index("is5m")] == 0 and late[ft.FEATURES.index("market_logit")] == pytest.approx(math.log(0.6 / 0.4))
    lean = ft.regime_from_rows([{"created_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "current_price": 100, "predicted_price_1d": 90,
                                 "confidence_1d": 70, "sentiment_score": -40}])
    assert lean.at(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()) == (pytest.approx(-0.2), -0.4) and lean.at(0) == (0.0, 0.0)


# ------------------------------------------------------------------ the alert rule and the scorecard
def test_signal_needs_confidence_edge_and_time():
    assert md.signal(0.95, 0.90, 0.12, 120, S) == ("up", 0.95, 0.90)
    assert md.signal(0.95, 0.93, 0.12, 120, S) is None          # 2 points of edge is not enough
    assert md.signal(0.95, 0.90, 0.12, 30, S) is None           # too late to place it
    assert md.signal(0.04, 0.10, 0.92, 120, S) == ("down", pytest.approx(0.96), 0.92)
    assert md.signal(0.85, 0.50, 0.50, 300, S) is None          # big edge, but not 90% sure
    assert md.side_cost(0.9, 0.05, 1) == pytest.approx(0.9045) and md.side_cost(None, 0.05, 1) is None


def test_weights_stored_as_json_reproduce_sklearn_exactly():
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier

    rng = np.random.default_rng(0)
    X = rng.normal(size=(3000, len(ft.FEATURES)))
    y = (X[:, 0] + 0.3 * rng.normal(size=3000) > 0).astype(int)
    for kind in ("logistic", "mlp"):
        params = md.fit(kind, X, y)
        json.dumps(params)                                       # plain numbers only
        Z = (X - np.array(params["mean"])) / np.array(params["std"])
        ref = (LogisticRegression(C=0.5, max_iter=2000) if kind == "logistic" else
               MLPClassifier(hidden_layer_sizes=(32, 16), activation="relu", alpha=1e-3, learning_rate_init=1e-3, early_stopping=True,
                             validation_fraction=0.15, n_iter_no_change=10, max_iter=300, random_state=0)).fit(Z, y)
        assert np.allclose(md.predict(params, X), ref.predict_proba(Z)[:, 1], atol=1e-6)
        assert md.scores(md.predict(params, X), y)["accuracy"] > 0.85


def test_backtest_counts_one_bet_per_window_and_side():
    d = {"y": np.array([1, 1, 0]), "window": np.array([1.0, 1.0, 2.0]), "up_cost": [0.8, 0.8, 0.8], "down_cost": [0.2, 0.2, 0.2],
         "secs_left": np.array([300, 200, 300])}
    bt = md.backtest(np.array([0.95, 0.96, 0.95]), d, S)
    assert bt["bets"] == 2 and bt["wins"] == 1 and bt["pnl_per_dollar"] == pytest.approx(0.25 - 1, abs=1e-3)


# ------------------------------------------------------------------ on a real Postgres
@pytest.fixture
def ud_db(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("truncate updown_windows, updown_models, btc_candles_1m, bankroll, bets_placed, assistant_state, bitcoin_bets, bitcoin_predictions cascade")
    return db


def synthetic_history(conn, days: float = 1.5, seed: int = 1):
    """A random-walk BTC, 15-minute windows resolved by it, and a market that prices them with the random-walk formula."""
    rng = np.random.default_rng(seed)
    t0 = (int(datetime.now(timezone.utc).timestamp()) // 900 - int(days * 96) - 2) * 900
    n = int(days * 1440) + 1500
    closes = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    times = t0 - 1440 * 60 + 60 * np.arange(n)
    with conn.cursor() as cur:
        cur.executemany("insert into btc_candles_1m (t, open, high, low, close) values (to_timestamp(%s), %s, %s, %s, %s)",
                        [(int(t), c, c, c, c) for t, c in zip(times, closes)])
    s = Series(times.astype(np.int64), closes)
    rows = []
    for k in range(int(days * 96)):
        start, end = t0 + k * 900, t0 + (k + 1) * 900
        K, final = s.close_at(start), s.close_at(end)
        slug = f"btc-updown-15m-{start}"
        conn.execute("""insert into updown_windows (slug, minutes, start_at, end_at, up_token, down_token, fee_rate, price_to_beat, basis, outcome)
                        values (%s, 15, to_timestamp(%s), to_timestamp(%s), 'u', 'd', 0.05, %s, 0, %s)""",
                     (slug, start, end, K, "up" if final >= K else "down"))
        for t in range(start + 30, end - 5, 60):
            price = s.close_at(t)
            z = math.log(price / K) / (0.0008 * math.sqrt((end - t) / 60))
            rows.append((slug, t, price, min(max(NormalDist().cdf(z), 0.01), 0.99)))
    with conn.cursor() as cur:
        cur.executemany("insert into updown_snapshots (slug, at, btc, up_price, source) values (%s, to_timestamp(%s), %s, %s, 'backfill')", rows)
    return s


@pytest.mark.integration
def test_training_reports_honestly_and_promotes_a_champion(ud_db):
    with psycopg.connect(ud_db, autocommit=True) as conn:
        synthetic_history(conn)
    r = md.train(S, min_windows=40)
    assert r["status"] == "trained" and r["promoted"] and r["kind"] in ("logistic", "mlp")
    assert r["windows_train"] > r["windows_validation"] > 0 and r["model"]["n"] == r["rows_validation"]
    # the market here IS the random-walk formula, so the model cannot be much better than it: no free money
    assert r["model"]["log_loss"] >= r["baselines"]["market"]["log_loss"] - 0.05
    ch = md.champion()
    assert ch["version"] == r["version"] and "Trained on" in md.describe(ch["metrics"])
    again = md.train(S, min_windows=40)
    assert again["champion_log_loss_before"] is not None                    # judged against the current champion


@pytest.mark.integration
def test_live_tick_signal_placed_bet_resolution_and_the_vault(ud_db, monkeypatch):
    from app import assistant
    from app.bitcoin import bankroll
    from app.bitcoin import candles as cdl
    from app.updown import live

    now = float(int(datetime.now(timezone.utc).timestamp()) // 900 * 900 + 600)   # 5 minutes left in the 15m window
    with psycopg.connect(ud_db, autocommit=True) as conn:
        synthetic_history(conn, days=0.5)
        # a champion that is always 97% sure of Up, already proven by its backtest
        params = md.to_params("logistic", np.zeros(len(ft.FEATURES)), np.ones(len(ft.FEATURES)), [(np.zeros((len(ft.FEATURES), 1)), np.array([3.5]))])
        conn.execute("insert into updown_models (version, kind, params, metrics, is_champion) values ('t1', 'logistic', %s, %s, true)",
                     (json.dumps(params), json.dumps({"proven": True})))
        conn.execute("insert into bankroll (balance, active, vault, peak) values (10, 10, 0, 10)")

    def event(minutes, start, closed=False, outcome=None):
        e = load("updown_15m_open.json")[0]
        m = e["markets"][0]
        e["slug"] = pm.updown_slug(minutes, start)
        m["cryptoMarketConfig"] = {"duration": f"{minutes}m"}
        m["eventStartTime"] = datetime.fromtimestamp(start, timezone.utc).isoformat()
        m["endDate"] = datetime.fromtimestamp(start + minutes * 60, timezone.utc).isoformat()
        m["closed"] = closed
        m["outcomePrices"] = json.dumps({"up": ["1", "0"], "down": ["0", "1"]}.get(outcome, ["0.9", "0.1"]))
        m["clobTokenIds"] = json.dumps([f"up{minutes}", f"down{minutes}"])
        if start < now - minutes * 60 + 1:                                        # a finished window: official numbers published
            e["eventMetadata"] = {"priceToBeat": 59_950.0, "finalPrice": 59_900.0}
        return e

    resolved: set[str] = set()
    monkeypatch.setattr(pm, "get_events", lambda http, slugs: [event(int(sl.split("-")[2][:-1]), int(sl.split("-")[3]),
                                                                      closed=sl in resolved, outcome="up" if sl in resolved else None) for sl in slugs])
    monkeypatch.setattr(pm, "get_book", lambda http, token: (0.88, 0.89) if token.startswith("up") else (0.10, 0.11))
    monkeypatch.setattr(cdl, "spot", lambda http: 60_000.0)
    monkeypatch.setattr(cdl, "sync", lambda http, a, b: 0)
    sent = []
    monkeypatch.setattr(assistant, "get_state", assistant.get_state)
    bot = assistant.Bot(S.model_copy(update={"telegram_bot_token": "x", "telegram_chat_id": "42"}))
    monkeypatch.setattr(bot, "send", lambda chat, text: sent.append(text))

    lp = live.Live(S, http=None, notify=bot.notify_signal, say=bot.notify)
    snaps = lp.tick(now)
    by_min = {x["minutes"]: x for x in snaps}
    assert set(by_min) == {5, 15} and by_min[15]["p_model"] == pytest.approx(0.9707, abs=1e-3) and by_min[15]["secs_left"] == 300
    assert by_min[15]["price_to_beat"] == 59_900.0                                      # the previous window's official final price
    assert by_min[15]["btc_index"] == pytest.approx(60_000 + lp.windows[by_min[15]["slug"]]["basis"])   # Coinbase + basis
    sig = by_min[15]["signal"]
    assert sig and sig["side"] == "up" and sig["proven"] and sig["cost"] == pytest.approx(0.89 + 0.05 * 0.89 * 0.11, abs=1e-4)
    assert 0 < sig["stake"] <= 3.0 and sent and sent[0].startswith("BET UP")             # stake capped at 30% of $10
    assert lp.tick(now + 10)[1]["signal"] is None                                        # one ping per window and side

    reply = assistant.handle(S, "in 2", now=datetime.fromtimestamp(now, timezone.utc))
    assert "Noted: $2.00 on UP" in reply and assistant.get_state("pending") is None
    resolved.add(sig["slug"])
    lp.resolve_due(now + 400)
    with psycopg.connect(ud_db) as conn:
        bet = conn.execute("select status, payout from bets_placed").fetchone()
    assert bet[0] == "won" and float(bet[1]) == pytest.approx(2 / sig["cost"], abs=0.01)
    assert "UP won. You won $" in sent[-1] and "Reply with your exact balance" in sent[-1]

    # the vault rule: best was $10; reporting $14 sets aside half of the $4 above it
    reply = assistant.handle(S, "14")
    assert "Set aside $2.00 now" in reply and bankroll.latest()["vault"] == 2.0
    assert "nothing to set aside" in assistant.handle(S, "12") and "$0.00" not in assistant.handle(S, "/reset 20")
    assert assistant.handle(S, "skip") == "Skipped. I will keep watching." and "BTC 5-minute" in assistant.handle(S, "/help")

    st = live.status(S)
    assert {w["minutes"] for w in st["windows"]} == {5, 15} and st["model"]["proven"] and st["signals"][0]["outcome"] == "up"
    assert st["record"] == {"settled": 1, "wins": 1, "pnl_per_dollar": pytest.approx(1 / sig["cost"] - 1, abs=1e-3)}


@pytest.mark.integration
def test_paper_signals_never_ask_you_to_bet(ud_db, monkeypatch):
    from app import assistant
    from app.updown import live

    payload = {"id": 1, "slug": "s", "side": "down", "p": 0.93, "cost": 0.85, "stake": 1.0, "proven": False, "end": 0, "label": "BTC 5m",
               "btc": 60_000, "price_to_beat": 60_100, "secs_left": 125, "multiplier": 1.18, "edge": 0.08, "active": 5, "advice": "",
               "min_order_usd": 4.25}
    text = live.signal_text(payload)
    assert text.startswith("PAPER SIGNAL DOWN") and "no real money" in text and '"in"' not in text
    sent = []
    bot = assistant.Bot(S.model_copy(update={"telegram_bot_token": "x", "telegram_chat_id": "42"}))
    monkeypatch.setattr(bot, "send", lambda chat, t: sent.append(t))
    bot.notify_signal(text, payload)
    assert sent and assistant.get_state("pending") is None                       # nothing to "place"
    assert "no open signal" in assistant.handle(S, "in")
    assistant.handle(S, "/pause")
    bot.notify_signal(text, payload)
    assert len(sent) == 1                                                        # paused: no ping


@pytest.mark.integration
def test_strangers_get_nothing_and_an_unconfigured_bot_only_tells_you_your_chat_id(ud_db, monkeypatch):
    import threading

    from app import assistant

    def run(chat_id_setting, from_chat):
        s = S.model_copy(update={"telegram_bot_token": "x", "telegram_chat_id": chat_id_setting})
        bot = assistant.Bot(s)
        sent, stop = [], threading.Event()
        calls = iter([[{"update_id": 7, "message": {"chat": {"id": from_chat}, "text": "12.40"}}]])

        def updates(offset, timeout=25):
            try:
                return next(calls)
            except StopIteration:
                stop.set()
                return []
        monkeypatch.setattr(bot, "updates", updates)
        monkeypatch.setattr(bot, "send", lambda chat, t: sent.append((chat, t)))
        assistant.poll_forever(s, bot, stop)
        return sent

    first = run("", 555)
    assert first == [("555", first[0][1])] and "TELEGRAM_CHAT_ID=555" in first[0][1]
    assert run("42", 999) == []                                                  # a stranger: no reply, no balance recorded
    from app.bitcoin import bankroll
    assert bankroll.latest() is None
    assert run("42", 42)[0][1].startswith("Got it: starting your bankroll at $12.40")


@pytest.mark.integration
def test_daily_plan_prices_every_target_against_the_forecast(btc_db, monkeypatch):  # noqa: F811
    from app.bitcoin import bankroll
    from app.bitcoin import candles as cdl
    from app.bitcoin.predictor import generate_prediction
    from app.updown import daily
    from tests.test_bitcoin import FakeBtcLLM

    with psycopg.connect(btc_db, autocommit=True) as conn:
        conn.execute("truncate bankroll, assistant_state cascade")
    generate_prediction(trigger="cli", llm=FakeBtcLLM())                         # spot 75,123.45, a mild Up lean
    bankroll.record(20, Settings())
    ev = load("daily_above.json")[0]
    for m in ev["markets"]:                                                       # re-centre the ladder on the fake spot
        m["groupItemTitle"] = f"{float(m['groupItemTitle'].replace(',', '')) + 15_000:,.0f}"
    monkeypatch.setattr(pm, "get_events", lambda http, slugs: [ev])
    monkeypatch.setattr(cdl, "spot", lambda http: 75_123.45)
    now = datetime(2026, 11, 27, 13, 0, tzinfo=timezone.utc)                       # 4 hours before noon ET
    p = daily.picks(Settings(), http=None, now=now)
    assert p["date"] == "2026-11-27" and p["hours"] == pytest.approx(4.0) and len(p["rows"]) >= 5
    probs = [r["p_yes"] for r in p["rows"]]
    assert probs == sorted(probs, reverse=True) and probs[0] > 0.95 and probs[-1] < 0.05    # higher targets are less likely
    if p["best"]:
        assert p["best"]["edge"] >= 0.05 and p["best"]["stake"] <= 20 * 0.30 + 1e-9
    text = daily.text(Settings(), p)
    assert "Daily plan for 2026-11-27" in text and "noon ET" in text and "Balance $20.00" in text
    assert daily.target_date(datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)).isoformat() == "2026-11-28"   # after noon ET: tomorrow


# ------------------------------------------------------------------ the live Chainlink feed
def test_chainlink_feed_parses_snapshots_and_updates_and_answers_by_time():
    from app.bitcoin.chainlink import Feed

    f = Feed(keep_s=600)
    snap = {"topic": "crypto_prices_chainlink", "type": "update", "timestamp": 0,         # the shape the socket really sends
            "payload": {"symbol": "btc/usd", "data": [{"timestamp": (1_000 + i) * 1000, "value": 60_000.0 + i} for i in range(60)]}}
    assert f.ingest(snap) == 60 and f.ingest(snap) == 0                                   # duplicates are not double-counted
    assert f.ingest({"payload": {"symbol": "btc/usd", "timestamp": 1_060_000, "value": 61_000.0}}) == 1
    assert f.ingest({"payload": {"symbol": "eth/usd", "timestamp": 1_061_000, "value": 1.0}}) == 0
    assert f.at(1_030) == 60_030.0 and f.at(1_062) == 61_000.0 and f.at(1_064) is None    # older than 3 s: unknown
    assert f.twap(1_060, 60) == pytest.approx(60_029.5) and f.twap(2_000, 60) is None      # [1000, 1060): a gap in the data: no average
    assert f.latest(now=1_065) == 61_000.0 and f.latest(now=1_100) is None                 # stale after 15 s
    f.ingest({"payload": {"symbol": "btc/usd", "timestamp": 2_000_000, "value": 1.0}})
    assert f.at(1_030) is None                                                             # older than keep_s is dropped


@pytest.mark.integration
def test_live_loop_prefers_the_chainlink_feed(ud_db, monkeypatch):
    from app.bitcoin import candles as cdl
    from app.bitcoin.chainlink import Feed
    from app.updown import live

    now = float(int(datetime.now(timezone.utc).timestamp()) // 900 * 900 + 300)
    with psycopg.connect(ud_db, autocommit=True) as conn:
        synthetic_history(conn, days=0.3)
    feed = Feed()
    start15 = now - 300  # the 15m window started 5 minutes ago
    feed.ingest({"payload": {"symbol": "btc/usd", "data": [{"timestamp": int(t) * 1000, "value": 60_100.0 if t >= start15 - 60 else 59_000.0}
                                                              for t in range(int(start15) - 120, int(now) + 1)]}})
    e = load("updown_15m_open.json")[0]

    def events(http, slugs):
        out = []
        for sl in slugs:
            minutes, start = int(sl.split("-")[2][:-1]), int(sl.split("-")[3])
            ev = json.loads(json.dumps(e))
            ev["slug"], m = sl, ev["markets"][0]
            m["cryptoMarketConfig"] = {"duration": f"{minutes}m"}
            m["eventStartTime"] = datetime.fromtimestamp(start, timezone.utc).isoformat()
            m["endDate"] = datetime.fromtimestamp(start + minutes * 60, timezone.utc).isoformat()
            out.append(ev)
        return out
    monkeypatch.setattr(pm, "get_events", events)
    monkeypatch.setattr(pm, "get_book", lambda http, token: (0.5, 0.51))
    monkeypatch.setattr(cdl, "sync", lambda http, a, b: 0)
    monkeypatch.setattr(cdl, "spot", lambda http: pytest.fail("Coinbase must not be needed while the feed is live"))
    snaps = {x["minutes"]: x for x in live.Live(S, http=None, feed=feed).tick(now)}
    assert snaps[15]["k_source"] == "chainlink" and snaps[15]["price_to_beat"] == pytest.approx(60_100.0)   # the 60 s before the start
    assert snaps[15]["btc"] == 60_100.0 and snaps[15]["price_source"] == "chainlink"


def test_the_telegram_bot_token_never_reaches_the_logs(caplog):
    import logging

    from app.logsafe import Redact, redact

    fake = "123456:" + "x" * 35                       # a made-up token
    url = f"GET https://api.telegram.org/bot{fake}/getUpdates?timeout=25"
    assert redact(url) == "GET https://api.telegram.org/bot<redacted>/getUpdates?timeout=25"
    caplog.handler.addFilter(Redact())
    logging.getLogger("httpx").warning("HTTP Request: %s", url)
    assert fake not in caplog.text and "bot<redacted>/getUpdates" in caplog.text
