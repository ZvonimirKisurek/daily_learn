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
def test_a_quote_becomes_the_markets_probability_and_trust_is_earned_from_settled_days():
    p, margin = betting.implied_up(1.02, 8.31)
    assert p == pytest.approx(0.8907, abs=1e-4) and margin == pytest.approx(0.1007, abs=1e-4)   # 98c + 12c = $1.10 for $1 of payout
    assert betting.blend(0.60, 0.90, 0.5) == pytest.approx(0.75) and betting.blend(0.60, 0.90, 0.0) == pytest.approx(0.90)
    assert betting.trust_from_record(0, 0) == 0.5                     # no history: the forecast and the market weigh the same
    assert betting.trust_from_record(8, 10) == 0.75 and betting.trust_from_record(0, 10) == 0.25 and betting.trust_from_record(3, 6) == 0.5


def test_a_favourite_with_an_edge_is_sized_by_half_kelly():
    r = betting.advise(0.65, 1.90, 1.90, 200.0, P)
    assert r["action"] == "BUY_YES" and r["multiplier"] == 1.90 and r["prob_win"] == 0.65 and not r["long_shot"]
    assert r["kelly_full"] == pytest.approx(0.2611, abs=1e-4)                      # (0.65 * 1.9 - 1) / 0.9
    assert r["cost"] == 26.11 and r["fraction"] == pytest.approx(0.1306, abs=1e-4)   # half of Kelly on $200
    assert r["max_profit"] == pytest.approx(23.50) and r["expected_return"] == pytest.approx(0.235)


def test_a_long_shot_gets_a_small_stake_and_the_cap_binds_on_a_huge_edge():
    r = betting.advise(0.70, 1.02, 8.31, 100.0, P)                                  # DOWN: 30% at 8.31x
    assert r["action"] == "BUY_NO" and r["long_shot"] and r["prob_win"] == pytest.approx(0.30) and "long shot" in r["reason"]
    assert r["kelly_full"] == pytest.approx(0.2042, abs=1e-4) and r["cost"] == 10.21 and r["max_profit"] == pytest.approx(74.64, abs=0.01)
    assert betting.advise(0.95, 1.50, 3.0, 100.0, P)["cost"] == 30.0                # Kelly says 42%; the cap is 30%


def test_a_small_bankroll_bets_the_sites_minimum_when_it_fits_under_the_cap():
    r = betting.advise(0.70, 1.90, 1.90, 4.0, P)                                    # half Kelly: $0.73; full Kelly allows $1.20
    assert r["action"] == "BUY_YES" and r["cost"] == 1.0 and r["fraction"] == 0.25 and "smallest bet" in r["reason"]
    assert betting.advise(0.60, 1.90, 1.90, 4.0, Params(min_trade=0.10))["cost"] == 0.31   # a site with 10c bets: plain Kelly


@pytest.mark.parametrize("p,up,down,active,action,needle", [
    (0.52, 1.90, 1.90, 200.0, "HOLD_CASH", "No edge"),                 # 52% vs a cost of 53%
    (0.89, 1.02, 8.31, 200.0, "HOLD_CASH", "No edge"),                 # we agree with the market: both sides cost more than they are worth
    (0.60, 1.90, 1.90, 4.0, "HOLD_CASH", "justifies at most $0.62"),   # full Kelly is 15.6% of $4: a $1 bet would over-bet
    (0.90, 1.90, 1.90, 3.0, "HOLD_CASH", "justifies at most $0.90"),   # a big edge, but the 30% cap is $0.90
    (0.65, 1.90, 1.90, 0.5, "HALT", "Trading stops"),                  # bankrupt: the vault is not touched
])
def test_no_bet_cases(p, up, down, active, action, needle):
    r = betting.advise(p, up, down, active, P)
    assert r["action"] == action and needle in r["reason"] and r["shares"] == 0 and r["cost"] == 0.0


def test_no_bet_means_ask_again_later_with_the_quotes_that_would_change_the_answer():
    r = betting.advise(0.52, 1.90, 1.90, 200.0, P)
    assert r["need_up_x"] == pytest.approx(2.04) and r["need_down_x"] == pytest.approx(2.22)   # 1 / (p - 3 points)
    assert betting.recheck_hours(20, 1, "HOLD_CASH") == 6.0 and betting.recheck_hours(7, 1, "HOLD_CASH") == 3.5
    assert betting.recheck_hours(20, 9, "HOLD_CASH") == 2.0             # the forecast is old: come back sooner
    assert betting.recheck_hours(1.5, 1, "HOLD_CASH") is None and betting.recheck_hours(20, 1, "BUY_YES") is None


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
            assert empty["state"] is None and empty["last"] is None and empty["history"] == [] and empty["params"]["kelly"] == 0.5
            assert 0 < empty["defaults"]["hours_to_settle"] <= 24 and "18:00" in empty["defaults"]["settle_label"]

            ask = {"balance": 200, "up_x": 2.5, "down_x": 1.5, "hours_to_settle": 24}          # what the site shows: balance + two quotes
            r = await c.post("/api/bitcoin/bet", json=ask)
            assert r.status_code == 409 and "no Bitcoin forecast" in r.json()["detail"]        # nothing to base a probability on yet

            generate_prediction(trigger="cli", llm=FakeBtcLLM())  # spot 75,123.45; 1d call: up, low confidence
            a = (await c.post("/api/bitcoin/bet", json=ask)).json()
            # no target = up/down from the current price. The site sells UP for 40% (2.5x); the model says a bit over 50%
            assert a["inputs"]["strike"] == 75_123.45 and a["model"]["shrink"] == 0.5 and 0.5 < a["model"]["p_yes"] < 0.6
            assert a["market"]["p_market"] == pytest.approx(0.375) and a["market"]["trust"] == 0.5 and a["record"]["n"] == 0
            assert a["market"]["p_used"] == pytest.approx((a["model"]["p_yes"] + 0.375) / 2, abs=1e-3)   # halfway: equal weight
            assert a["action"] == "BUY_YES" and a["multiplier"] == 2.5 and 0 < a["cost"] <= 60.0 and a["next_check"] is None
            assert a["bankroll"] == {"balance": 200.0, "active": 200.0, "vault": 0.0, "peak": 200.0, "swept": 0.0, "pnl": None}

            chk = (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.80})).json()   # the market now overpays: sell
            assert chk["action"] == "SELL" and chk["rule"] == "exit" and chk["side"] == "BUY_YES" and chk["shares"] > 0
            held = (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.42, "paid_price": 0.40})).json()
            assert held["action"] == "HOLD" and held["entry_price"] == 0.40

            # next day: the balance grew to $230 -> a new peak, half of the $30 gain is vaulted. Fair quotes: nothing to bet on
            b = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 230, "up_x": 1.85, "down_x": 1.85})).json()
            assert b["bankroll"] == {"balance": 230.0, "active": 215.0, "vault": 15.0, "peak": 230.0, "swept": 15.0, "pnl": 30.0}
            assert b["action"] == "HOLD_CASH" and b["cost"] == 0.0 and b["next_check"]["hours"] == 6.0 and b["need_up_x"] > 1.85
            assert (await c.post("/api/bitcoin/bet/check", json={"sell_price": 0.5})).status_code == 409   # no open position

            # a losing day: nothing comes back out of the vault. A lopsided quote with no target entered earns a warning
            d = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 180, "up_x": 1.02, "down_x": 8.31, "min_bet": 0.5})).json()
            assert d["bankroll"]["vault"] == 15.0 and d["bankroll"]["active"] == 165.0 and d["bankroll"]["pnl"] == -50.0
            assert d["action"] == "BUY_NO" and d["long_shot"] and d["inputs"]["min_bet"] == 0.5
            assert any("price to beat" in w for w in d["warnings"])
            assert (await c.post("/api/bitcoin/bet", json=ask | {"balance": -1})).status_code == 422
            assert (await c.post("/api/bitcoin/bet", json={"balance": 200})).status_code == 422          # the quotes are required
            assert (await c.post("/api/bitcoin/bet", json=ask | {"up_x": 0.9})).status_code == 422       # a multiplier is above 1

            fresh = (await c.post("/api/bitcoin/bet", json=ask | {"balance": 50, "reset": True})).json()   # after a deposit/withdrawal
            assert fresh["bankroll"] == {"balance": 50.0, "active": 50.0, "vault": 0.0, "peak": 50.0, "swept": 0.0, "pnl": None}

            o = (await c.get("/api/bitcoin/bet")).json()
            assert o["state"]["active"] == 50.0 and len(o["history"]) == 4 and o["history"][0]["is_reset"] and o["last"]["id"] == fresh["id"]
            assert [h["action"] for h in o["history"]][1:] == ["BUY_NO", "HOLD_CASH", "BUY_YES"] and o["record"]["trust"] == 0.5 and o["defaults"]["min_bet"] == 1.0 and "Not financial advice" in o["disclaimer"]

            # learning from settled days: on both, BTC ended above the target. The model was closer once, the market once
            from datetime import datetime, timedelta, timezone
            from psycopg.types.json import Jsonb
            with psycopg.connect(btc_db, autocommit=True) as conn:
                for days_ago, p_model in ((2, 0.70), (3, 0.30)):
                    at = datetime.now(timezone.utc) - timedelta(days=days_ago)
                    conn.execute("insert into bitcoin_prices (price_usd, source, fetched_at) values (72000, 'test', %s)", (at + timedelta(minutes=20),))
                    detail = {"inputs": {"strike": 70_000, "settle_at": at.isoformat(), "up_x": 2.5, "down_x": 1.5}, "cost": 5,
                              "model": {"p_yes": p_model}, "market": {"p_market": 0.4}}
                    conn.execute("insert into bitcoin_bets (created_at, balance, active, vault, peak, action, detail) values (%s, 10, 10, 0, 10, 'BUY_YES', %s)",
                                 (at - timedelta(hours=5), Jsonb(detail)))
            rec = (await c.get("/api/bitcoin/bet")).json()["record"]
            assert rec["n"] == 2 and rec["model_closer"] == 1 and rec["bets"] == 2 and rec["wins"] == 2 and rec["trust"] == 0.5
            assert "closer to the outcome than the market on 1" in rec["note"] and "2 of 2 recommended bets won" in rec["note"]
    with psycopg.connect(btc_db) as conn:
        assert conn.execute("select count(*) from bitcoin_bets").fetchone()[0] == 6
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
