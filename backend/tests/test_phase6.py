import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from fastapi import HTTPException

from app import retro
from app.api.deps import require_token
from app.config import Settings, get_settings
from app.curriculum import usage
from app.curriculum.errors import LLMBudgetExceeded
from app.curriculum.schemas import DailyPlanOutput, GroundingContext
from app.curriculum.summaries import summarize_pending
from app.main import create_app
from tests.factories import daily_dict
from tests.llm_helpers import completion, make_llm
from tests.test_integration_db import seed_articles


# ------------------------------------------------------------------ cost maths (unit)
def test_estimate_cost_uses_price_table_and_is_conservative_for_unknown_models():
    assert usage.estimate_cost("gpt-4o", 11_000, 5_000) == pytest.approx(0.0275 + 0.05)
    assert usage.estimate_cost("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    assert usage.estimate_cost("some-future-model", 1_000_000, 1_000_000) == pytest.approx(60.0)  # assumes the priciest tier


# ------------------------------------------------------------------ token rotation (unit)
def call(settings, header):
    return asyncio.run(require_token(settings, header))


def test_api_token_rotation_accepts_old_and_new_but_nothing_else():
    s = Settings(api_token="new-token, old-token")
    assert call(s, "Bearer new-token") is None and call(s, "Bearer old-token") is None
    for bad in ("Bearer nope", "Bearer ", "Basic new-token", None):
        with pytest.raises(HTTPException) as e:
            call(s, bad)
        assert e.value.status_code == 401
    assert call(Settings(api_token=""), None) is None  # open in dev


# ------------------------------------------------------------------ retro (unit)
def row(day, kind, minutes_after=None, skill="rag", pts=0.5, base=datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)):
    return {"day": day, "kind": kind, "skill_slug": skill, "title": "t", "points_awarded": pts if minutes_after is not None else None,
            "completed_at": None if minutes_after is None else base + timedelta(days=(day - date(2026, 11, 3)).days, minutes=minutes_after)}


def test_retro_flags_click_through_and_reading_only_weeks():
    d1, d2 = date(2026, 11, 3), date(2026, 11, 4)
    clicky = [row(d1, "reading", 0), row(d1, "hands_on", 1), row(d1, "insight", 2), row(d2, "reading", 0), row(d2, "hands_on"), row(d2, "insight")]
    a = retro.analyze(clicky)
    assert a["done"] == 4 and a["burst"] and a["by_kind"]["hands_on"] == (1, 2) and a["streak"] == 2
    text = retro.render(a, ["Milestone A"])
    assert "[!] ticks clustered" in text and "[ ] Milestone A" in text and "artifact" in text

    healthy = [row(d1, "reading", 0), row(d1, "hands_on", 120), row(d1, "insight", 400), row(d2, "reading", 0), row(d2, "hands_on", 150), row(d2, "insight", 300)]
    b = retro.analyze(healthy)
    assert not b["burst"] and b["by_kind"]["hands_on"] == (2, 2) and b["rate"] == 1.0
    assert "[!]" not in retro.render(b)
    assert retro.render(retro.analyze([])) == "no briefings in the last 7 days"


# ------------------------------------------------------------------ cap + ledger (real DB)
def llm_with_transport(settings, in_tok=10_000, out_tok=5_000):
    return make_llm([completion(json.dumps(daily_dict("A1")), prompt=in_tok, out=out_tok)] * 10, **settings)


CTX = GroundingContext(frozenset({"A1", "A2"}), frozenset({"rag", "vector-db", "pytorch"}))


@pytest.mark.integration
def test_every_call_is_recorded_and_the_cap_refuses_before_spending(db):
    s = dict(llm_daily_budget_usd=0.10)
    llm, sent = llm_with_transport(s)
    llm.generate(system="s", user="u", output_model=DailyPlanOutput, grounding=CTX)   # gpt-4o: 10k in * $2.5/M + 5k out * $10/M = $0.075
    assert usage.spent_last_24h() == pytest.approx(0.075, abs=1e-4)
    llm.generate(system="s", user="u", output_model=DailyPlanOutput, grounding=CTX)   # allowed (0.075 < 0.10), now $0.15
    assert len(sent) == 2
    with pytest.raises(LLMBudgetExceeded, match="cap is \\$0.10"):
        llm.generate(system="s", user="u", output_model=DailyPlanOutput, grounding=CTX)
    assert len(sent) == 2, "the refused call must never reach the API"
    assert "DailyPlanOutput" in usage.usage_report() and "$0.150" in usage.usage_report()


@pytest.mark.integration
def test_old_spend_does_not_count_against_todays_cap(db):
    with psycopg.connect(db) as c:
        c.execute("insert into llm_usage (at, purpose, model, input_tokens, output_tokens, cost_usd) values (now() - interval '2 days', 'x', 'm', 1, 1, 50)")
    assert usage.spent_last_24h() == 0
    usage.DbUsage(Settings(llm_daily_budget_usd=1.0)).check()  # no raise


@pytest.mark.integration
def test_summarizer_stops_at_the_cap_and_leaves_articles_pending(db):
    seed_articles(12)
    with psycopg.connect(db) as c:
        c.execute("insert into llm_usage (purpose, model, input_tokens, output_tokens, cost_usd) values ('x', 'm', 1, 1, 5)")
    llm, sent = llm_with_transport(dict(llm_daily_budget_usd=1.0, summary_batch_size=5))
    out = summarize_pending(llm=llm, settings=Settings(llm_daily_budget_usd=1.0, summary_batch_size=5))
    assert out["summarized"] == 0 and sent == []
    with psycopg.connect(db) as c:
        assert c.execute("select count(*) from articles where ai_summary is null").fetchone()[0] == 12


# ------------------------------------------------------------------ export + retro against the DB, docs toggle
@pytest.mark.integration
def test_export_progress_and_retro_fetch_work_on_real_schema(db, tmp_path):
    from tests.test_integration_db import FakeEmbedder, FakeLLM
    from app.curriculum import generators
    seed_articles()
    generators.generate_daily_briefing(llm=FakeLLM(), embedder=FakeEmbedder(), settings=Settings(timezone="UTC", min_candidates=10))
    out = retro.export_progress(tmp_path / "p.json")
    assert out["skills"] == 8 and out["daily_tasks"] == 3 and out["daily_briefings"] == 1
    dumped = json.loads((tmp_path / "p.json").read_text())
    assert "embedding" not in dumped["skills"][0] and dumped["daily_tasks"][0]["kind"] in ("reading", "hands_on", "insight")
    a = retro.analyze(retro.fetch_rows())
    assert a["total"] == 3 and a["done"] == 0
    assert isinstance(retro.latest_milestones(), list)


@pytest.mark.integration
async def test_docs_can_be_disabled_for_production(db, monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    get_settings.cache_clear()
    for enabled, expected in ((True, 200), (False, 404)):
        app = create_app(Settings(enable_docs=enabled))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                assert (await c.get("/openapi.json")).status_code == expected
                assert (await c.get("/docs")).status_code == expected
                assert (await c.get("/health")).status_code == 200
    get_settings.cache_clear()
