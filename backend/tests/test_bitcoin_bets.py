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
def test_bet_up_is_sized_by_quarter_kelly_at_the_highest_price_worth_paying():
    r = betting.advise(0.65, 0.10, 200.0, P)
    assert r["action"] == "BUY_YES" and r["prob_win"] == 0.65 and r["entry_price"] == 0.60     # max price = p - min_edge
    assert r["kelly_full"] == pytest.approx(0.125) and r["fraction"] == pytest.approx(0.03125, abs=1e-4)   # 0.05 / (1 - 0.60), then a quarter
    assert r["cost"] == 6.25 and r["shares"] == 10 and "60c or less" in r["reason"]            # $200 * 3.125%
    assert r["max_profit"] == pytest.approx(4.17) and r["expected_profit"] == pytest.approx(0.52)


def test_bet_down_follows_a_negative_lean_and_the_risk_cap_binds():
    r = betting.advise(0.20, -0.10, 1_000.0, P)
    assert r["action"] == "BUY_NO" and r["prob_win"] == pytest.approx(0.80) and r["entry_price"] == 0.75 and r["cost"] == 50.0
    capped = betting.advise(0.95, 0.20, 1_000.0, P)                                             # Kelly says 12.5%; the cap is 10%
    assert capped["fraction"] == 0.10 and capped["cost"] == 100.0


@pytest.mark.parametrize("p,lean,active,action,needle", [
    (0.65, 0.0, 200.0, "HOLD_CASH", "No edge"),                    # no calibrated lean: a coin flip
    (0.65, 0.005, 200.0, "HOLD_CASH", "No edge"),                  # a lean under one point is noise
    (0.65, 0.10, 8.0, "HOLD_CASH", "below the minimum order"),     # 3.1% of $8 = $0.25
    (0.05, 0.10, 200.0, "HOLD_CASH", "too unlikely"),              # the strike is far above: no price is low enough
    (0.65, 0.10, 0.5, "HALT", "Trading stops"),                    # bankrupt: the vault is not touched
])
def test_no_bet_cases(p, lean, active, action, needle):
    r = betting.advise(p, lean, active, P)
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

            ask = {"balance": 200, "hours_to_settle": 24}                                       # the balance is the only thing you report
            r = await c.post("/api/bitcoin/bet", json=ask)
            assert r.status_code == 409 and "no Bitcoin forecast" in r.json()["detail"]        # nothing to base a probability on yet

            generate_prediction(trigger="cli", llm=FakeBtcLLM())  # spot 75,123.45; 1d call: up, low confidence
            a = (await c.post("/api/bitcoin/bet", json=ask)).json()
            # no strike = up/down from the current price; no scored track record: the stated edge is halved
            assert a["inputs"]["strike"] == 75_123.45 and a["model"]["shrink"] == 0.5 and 0.5 < a["model"]["p_yes"] < 0.6
            assert a["action"] == "BUY_YES" and 0 < a["cost"] <= 20.0                           # <= 10% of $200
            assert a["entry_price"] == pytest.approx(a["prob_win"] - 0.05, abs=0.006)           # the highest price worth paying
            assert a["bankroll"] == {"balance": 200.0, "active": 200.0, "vault": 0.0, "peak": 200.0, "swept": 0.0, "pnl": None}

            chk = (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.80})).json()   # the market now overpays: sell
            assert chk["action"] == "SELL" and chk["rule"] == "exit" and chk["side"] == "BUY_YES" and chk["shares"] > 0
            held = (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.42, "paid_price": 0.40})).json()
            assert held["action"] == "HOLD" and held["entry_price"] == 0.40

            # next day: the balance grew to $230 -> a new peak, half of the $30 gain is vaulted
            b = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 230})).json()
            assert b["bankroll"] == {"balance": 230.0, "active": 215.0, "vault": 15.0, "peak": 230.0, "swept": 15.0, "pnl": 30.0}
            assert b["action"] == "BUY_YES"

            # a losing day: nothing comes back out of the vault; a strike far above the price is too unlikely to bet on
            d = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 180, "strike": 90_000})).json()
            assert d["bankroll"]["vault"] == 15.0 and d["bankroll"]["active"] == 165.0 and d["bankroll"]["pnl"] == -50.0
            assert d["action"] == "HOLD_CASH" and d["cost"] == 0.0
            assert (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.5})).status_code == 409   # no open position
            assert (await c.post("/api/bitcoin/bet", json=ask | {"balance": -1})).status_code == 422

            fresh = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 50, "reset": True})).json()   # after a deposit/withdrawal
            assert fresh["bankroll"] == {"balance": 50.0, "active": 50.0, "vault": 0.0, "peak": 50.0, "swept": 0.0, "pnl": None}

            o = (await c.get("/api/bitcoin/bet")).json()
            assert o["state"]["active"] == 50.0 and len(o["history"]) == 4 and o["history"][0]["is_reset"] and o["last"]["id"] == fresh["id"]
            assert [h["action"] for h in o["history"]][1:] == ["HOLD_CASH", "BUY_YES", "BUY_YES"] and "Not financial advice" in o["disclaimer"]
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
