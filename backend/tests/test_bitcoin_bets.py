"""Bitcoin bet advisor: the sizing arithmetic (pure) and the daily check-in over the real schema."""
import httpx
import psycopg
import pytest

from app.bitcoin import betting
from app.bitcoin.betting import Params
from tests.test_bitcoin import FakeBtcLLM, btc_db  # noqa: F401  (btc_db is a fixture)

P = Params()


# ------------------------------------------------------------------ probability
def test_probability_model_reduces_to_the_stated_confidence_and_the_random_walk():
    assert betting.prob_above(75_000, 75_000, 0.02, 24, 0.62) == pytest.approx(0.62, abs=1e-6)   # strike = spot, 1 day: c itself
    assert betting.prob_above(75_000, 75_000, 0.02, 24, 0.5) == pytest.approx(0.5)
    far, near = betting.prob_above(75_000, 78_000, 0.02, 24, 0.5), betting.prob_above(75_000, 76_000, 0.02, 24, 0.5)
    assert far < near < 0.5                                                                       # further above spot = less likely
    assert betting.prob_above(75_000, 78_000, 0.02, 48, 0.5) > far                                # more time = more chance to get there
    assert betting.prob_above(77_990, 78_000, 0.02, 0.5, 0.9) < 0.6                               # 30 minutes left: drift barely matters
    assert betting.prob_up_1d(76_000, 75_000, 70, 1.0) == (0.7, 0.7)
    assert betting.prob_up_1d(74_000, 75_000, 70, 0.5) == pytest.approx((0.3, 0.4))               # bearish call, half trusted


@pytest.mark.parametrize("track,expect,needle", [
    (None, 0.5, "only 0 scored"),
    ({"n": 3, "hit_rate_pct": 100.0, "mean_confidence": 60.0}, 0.5, "only 3 scored"),             # too few to believe
    ({"n": 12, "hit_rate_pct": 50.0, "mean_confidence": 66.0}, 0.0, "coin flip"),                 # no edge: no directional view
    ({"n": 12, "hit_rate_pct": 58.0, "mean_confidence": 66.0}, 0.5, "58% of the time vs 66%"),    # overconfident: halve the edge
    ({"n": 12, "hit_rate_pct": 75.0, "mean_confidence": 60.0}, 1.0, "75%"),                       # never trusted beyond what it stated
])
def test_calibration_shrinks_the_stated_edge_by_the_track_record(track, expect, needle):
    shrink, note = betting.calibration_shrink(track)
    assert shrink == pytest.approx(expect) and needle in note


# ------------------------------------------------------------------ direction + sizing (the spec's daily routine)
def test_buy_yes_when_the_model_beats_the_ask_sized_by_quarter_kelly():
    r = betting.advise(0.65, 0.50, 0.48, 200.0, P)
    assert r["action"] == "BUY_YES" and r["entry_price"] == 0.5 and r["kelly_full"] == pytest.approx(0.30)   # (p - S) / (1 - S)
    assert r["fraction"] == pytest.approx(0.075) and r["shares"] == 30 and r["cost"] == 15.0                 # 0.25 * 0.30 * $200 = $15
    assert r["take_profit"] == pytest.approx(0.85) and r["stop_loss"] == pytest.approx(0.30)
    assert r["max_profit"] == 15.0 and r["expected_profit"] == pytest.approx(4.5)


def test_buy_no_uses_one_minus_the_bid_and_the_risk_cap_binds():
    r = betting.advise(0.20, 0.72, 0.70, 1_000.0, P)
    assert r["action"] == "BUY_NO" and r["entry_price"] == pytest.approx(0.30) and r["prob_win"] == pytest.approx(0.80)
    assert r["kelly_full"] == pytest.approx(5 / 7, abs=1e-4)             # (S_bid - p) / S_bid
    assert r["fraction"] == 0.10 and r["shares"] == 333 and r["cost"] == pytest.approx(99.9)   # capped at 10% of the bankroll


@pytest.mark.parametrize("p,ask,bid,active,params,action,needle", [
    (0.54, 0.50, 0.48, 200.0, P, "HOLD_CASH", "No edge"),                               # 4 points < 5
    (0.50, 0.51, 0.49, 200.0, P, "HOLD_CASH", "No edge"),                               # inside the spread
    (0.65, 0.50, 0.48, 8.0, P, "HOLD_CASH", "below the minimum order"),                 # 7.5% of $8 = $0.60
    (0.65, 0.50, 0.48, 0.5, P, "HALT", "Trading stops"),                                # bankrupt: the vault is not touched
    (0.56, 0.50, 0.48, 200.0, Params(fee=0.15), "HOLD_CASH", "does not cover"),         # 12% expected return vs a 15% fee
])
def test_no_bet_cases(p, ask, bid, active, params, action, needle):
    r = betting.advise(p, ask, bid, active, params)
    assert r["action"] == action and needle in r["reason"] and r["shares"] == 0 and r["cost"] == 0.0


# ------------------------------------------------------------------ vault (high-water mark sweep)
def test_half_of_every_new_peak_is_vaulted_and_the_vault_never_shrinks():
    vault, peak = 0.0, 100.0
    seen = []
    for balance in (100.0, 120.0, 110.0, 119.0, 140.0, 20.0, 10.0):
        active, new_vault, peak, swept = betting.settle(balance, vault, peak)
        assert new_vault >= vault and active == pytest.approx(max(0.0, balance - new_vault)) and swept == pytest.approx(new_vault - vault)
        vault = new_vault
        seen.append((active, vault, peak))
    assert seen == [(100.0, 0.0, 100.0), (110.0, 10.0, 120.0), (100.0, 10.0, 120.0), (109.0, 10.0, 120.0),   # recovery below the peak: no sweep
                    (120.0, 20.0, 140.0), (0.0, 20.0, 140.0), (0.0, 20.0, 140.0)]                              # losses never pull from the vault


# ------------------------------------------------------------------ selling before settlement
@pytest.mark.parametrize("sell,p_now,rule", [
    (0.70, 0.66, "exit"),            # the market pays more than the model thinks it is worth
    (0.86, 0.95, "take_profit"),     # 72% of the 50c upside captured
    (0.29, 0.60, "stop_loss"),       # down 42% of cost
    (0.55, 0.66, "hold"),
])
def test_position_check_rules(sell, p_now, rule):
    r = betting.check_position(0.50, sell, p_now, 30, P)
    assert r["rule"] == rule and r["action"] == ("HOLD" if rule == "hold" else "SELL") and r["unrealized"] == pytest.approx(30 * (sell - 0.5))


# ------------------------------------------------------------------ API over the real schema
@pytest.mark.integration
async def test_daily_check_in_flow(btc_db, monkeypatch):  # noqa: F811
    from app.bitcoin.predictor import generate_prediction
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("API_TOKEN", "")
    get_settings.cache_clear()
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            empty = (await c.get("/api/bitcoin/bet")).json()
            assert empty["state"] is None and empty["last"] is None and empty["history"] == [] and empty["params"]["kelly"] == 0.25
            assert 0 < empty["defaults"]["hours_to_settle"] <= 24 and "18:00" in empty["defaults"]["settle_label"]

            ask = {"balance": 200, "strike": 75_123.45, "yes_ask": 0.30, "yes_bid": 0.28, "hours_to_settle": 24}
            r = await c.post("/api/bitcoin/bet", json=ask)
            assert r.status_code == 409 and "no Bitcoin forecast" in r.json()["detail"]        # nothing to base a probability on yet

            generate_prediction(trigger="cli", llm=FakeBtcLLM())  # spot 75,123.45; 1d call: up, low confidence
            a = (await c.post("/api/bitcoin/bet", json=ask)).json()
            # strike = spot and no scored track record: the stated edge is halved, so p(YES) sits just above 50%
            assert a["model"]["shrink"] == 0.5 and 0.5 <= a["model"]["p_yes"] < 0.6 and a["market"]["spot"] == 75_123.45
            assert a["action"] == "BUY_YES" and a["entry_price"] == 0.30 and a["shares"] > 0 and a["cost"] <= 20.0    # <= 10% of $200
            assert a["bankroll"] == {"balance": 200.0, "active": 200.0, "vault": 0.0, "peak": 200.0, "swept": 0.0, "pnl": None}

            chk = (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.80})).json()   # the market now overpays: sell
            assert chk["action"] == "SELL" and chk["rule"] == "exit" and chk["side"] == "BUY_YES" and chk["shares"] == a["shares"]
            assert (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.35})).json()["action"] == "HOLD"

            # next day: the balance grew to $230 -> a new peak, half of the $30 gain is vaulted; the market is now fairly priced
            b = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 230, "yes_ask": 0.56, "yes_bid": 0.52})).json()
            assert b["bankroll"] == {"balance": 230.0, "active": 215.0, "vault": 15.0, "peak": 230.0, "swept": 15.0, "pnl": 30.0}
            assert b["action"] == "HOLD_CASH" and b["shares"] == 0
            assert (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.5})).status_code == 409   # no open position

            # a losing day: nothing comes back out of the vault; a far-away strike makes NO the side with the edge
            d = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 180, "strike": 90_000, "yes_ask": 0.40, "yes_bid": 0.38})).json()
            assert d["bankroll"]["vault"] == 15.0 and d["bankroll"]["active"] == 165.0 and d["bankroll"]["pnl"] == -50.0
            assert d["action"] == "BUY_NO" and d["entry_price"] == pytest.approx(0.62) and d["cost"] <= 16.5

            bad = await c.post("/api/bitcoin/bet", json=ask | {"yes_ask": 0.30, "yes_bid": 0.40})
            assert bad.status_code == 422 and "cannot be higher" in bad.json()["detail"]
            assert (await c.post("/api/bitcoin/bet", json=ask | {"yes_ask": 1.2})).status_code == 422

            fresh = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 50, "reset": True})).json()   # after a deposit/withdrawal
            assert fresh["bankroll"] == {"balance": 50.0, "active": 50.0, "vault": 0.0, "peak": 50.0, "swept": 0.0, "pnl": None}

            o = (await c.get("/api/bitcoin/bet")).json()
            assert o["state"]["active"] == 50.0 and len(o["history"]) == 4 and o["history"][0]["is_reset"] and o["last"]["id"] == fresh["id"]
            assert [h["action"] for h in o["history"]][1:] == ["BUY_NO", "HOLD_CASH", "BUY_YES"] and "Not financial advice" in o["disclaimer"]
    with psycopg.connect(btc_db) as conn:
        assert conn.execute("select count(*) from bitcoin_bets").fetchone()[0] == 4
    get_settings.cache_clear()


def test_the_model_is_never_certain_even_far_from_the_strike():
    assert betting.prob_above(75_000, 60_000, 0.02, 24, 0.5) == 0.98 and betting.prob_above(75_000, 95_000, 0.02, 24, 0.5) == 0.02


# ------------------------------------------------------------------ live while the page is open
@pytest.mark.integration
def test_pulse_refreshes_cheaply_and_forecasts_only_on_news_or_staleness(btc_db, monkeypatch):  # noqa: F811
    from app.bitcoin import ingest, live
    from app.bitcoin.predictor import generate_prediction
    from app.config import Settings
    from app.tasks import generate_bitcoin_daily_insight

    s = Settings(bitcoin_live_price_seconds=0, bitcoin_live_news_minutes=0, bitcoin_live_min_minutes=30)
    jobs = []
    enqueue = lambda name, **kw: jobs.append((name, kw))  # noqa: E731
    monkeypatch.setattr(live, "_last_news_fetch", 0.0)

    first = live.pulse(s, enqueue)                                   # no forecast yet: fetch the price, start one
    assert first["price_updated"] and first["forecast"] == "started" and "no forecast" in first["reason"]
    assert jobs == [("app.tasks.generate_bitcoin_daily_insight", {"trigger": "live", "run_id": str(first["run_id"])})]
    assert live.pulse(s, enqueue)["forecast"] == "running" and len(jobs) == 1   # a second tab joins the run in flight

    generate_prediction(trigger="live", llm=FakeBtcLLM(), run_id=str(first["run_id"]))
    assert live.pulse(s, enqueue)["forecast"] == "current" and len(jobs) == 1   # nothing new: no AI call

    def news(n):
        with psycopg.connect(btc_db, autocommit=True) as conn:
            conn.execute("insert into bitcoin_news (source_name, title, url, url_hash, btc_relevant, published_at) values ('X', 'BTC jumps', %s, %s, true, now())",
                         (f"https://n.test/new{n}", f"new{n}"))
    news(1)
    cool = live.pulse(s, enqueue)                                    # news, but the last forecast is minutes old
    assert cool["forecast"] == "cooldown" and len(jobs) == 1
    with psycopg.connect(btc_db, autocommit=True) as conn:
        conn.execute("update bitcoin_predictions set created_at = created_at - interval '2 hours'")
        conn.execute("update bitcoin_runs set finished_at = finished_at - interval '2 hours', started_at = started_at - interval '2 hours'")
        conn.execute("update bitcoin_news set fetched_at = now() where url_hash = 'new1'")
    hit = live.pulse(s, enqueue)
    assert hit["forecast"] == "started" and "new Bitcoin headline" in hit["reason"] and len(jobs) == 2 and jobs[1][1]["trigger"] == "live"

    monkeypatch.setattr(ingest, "fetch_spot_price", lambda s=None: (_ for _ in ()).throw(RuntimeError("exchanges down")))
    assert live.pulse(s, enqueue)["price_updated"] is False          # a failed price fetch is survivable

    # nobody looking = nothing happens: the scheduled run is a no-op unless explicitly enabled
    monkeypatch.setenv("BITCOIN_BACKGROUND_FORECAST", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    assert "skipped" in generate_bitcoin_daily_insight.run(trigger="beat")
    get_settings.cache_clear()
