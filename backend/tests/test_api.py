"""API integration tests: real FastAPI app (lifespan + async psycopg pool) over embedded Postgres+pgvector."""
import asyncio
from datetime import date, datetime, timezone

import httpx
import numpy as np
import psycopg
import pytest

from app.config import Settings, get_settings
from app.curriculum import generators, summaries
from app.curriculum.schemas import SummaryBatch
from app.ingestion import repository as irepo
from app.ingestion.models import RawItem
from app.api.deps import get_llm
from app.main import create_app
from tests.factories import unit_vec
from tests.test_integration_db import FakeEmbedder, FakeLLM, seed_articles

pytestmark = pytest.mark.integration
rng = np.random.default_rng(7)
TODAY = datetime.now(timezone.utc).date()


@pytest.fixture
async def client(db, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")  # an empty env var beats a token in a local .env file (delenv would not)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_llm] = lambda: FakeLLM()  # grading never touches OpenAI in tests
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            c.app = app
            yield c
    get_settings.cache_clear()


def generate_today():
    s = Settings(timezone="UTC", min_candidates=10, prompt_article_count=20)
    generators.generate_daily_briefing(day=TODAY, llm=FakeLLM(), embedder=FakeEmbedder(), settings=s)


def task_ids(db):
    with psycopg.connect(db) as conn:
        return {k: str(i) for i, k in conn.execute("select id, kind from daily_tasks")}


# --------------------------------------------------------------------------------------------- today
async def test_today_pending_before_generation(client):
    r = await client.get("/api/today")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "pending" and body["briefing"] is None and body["weekly_theme"] is None
    assert len(body["skills"]) == 7


async def test_today_ready_has_tasks_theme_and_resolved_urls(client, db):
    seed_articles()
    generate_today()
    body = (await client.get("/api/today")).json()
    assert body["state"] == "ready"
    assert [t["kind"] for t in body["briefing"]["tasks"]] == ["reading", "hands_on", "insight"]
    assert body["briefing"]["payload"]["morning_reading"]["inspiration"][0]["url"].startswith("https://s.test/")
    assert body["briefing"]["payload"]["market_insight"]["word_count"] >= 450 and "content" not in body["briefing"]["payload"]["morning_reading"]
    assert [t["has_content"] for t in body["briefing"]["tasks"]] == [True, False, False]  # the chapter is fetched on demand, not in /today
    assert body["weekly_theme"]["theme"]["title"] and len(body["weekly_theme"]["theme"]["daily_plan"]) == 7
    assert set(body) == {"date", "state", "briefing", "weekly_theme", "skills"}  # response_model fixes the contract
    assert set(body["briefing"]) == {"id", "day", "completed_at", "payload", "tasks"}


# --------------------------------------------------------------------------------------------- completion
async def test_complete_moves_skill_and_is_idempotent(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["reading"]
    r1 = (await client.post(f"/api/tasks/{tid}/complete")).json()
    assert not r1["already_completed"] and r1["skill"]["delta"] > 0 and r1["task"]["completed"]
    r2 = (await client.post(f"/api/tasks/{tid}/complete")).json()
    assert r2["already_completed"] and r2["skill"]["level"] == pytest.approx(r1["skill"]["level"], abs=1e-3)
    sk = {s["slug"]: s for s in (await client.get("/api/skills")).json()}[r1["skill"]["slug"]]
    assert sk["level"] == pytest.approx(r1["skill"]["level"], abs=0.01) and sk["delta_7d"] == pytest.approx(r1["skill"]["delta"], abs=1e-3)
    assert sk["percent_to_target"] == pytest.approx(sk["level"] / sk["target"] * 100, abs=0.1) and len(sk["history"]) == 1


async def test_commit_is_visible_to_other_connections_when_response_arrives(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["reading"]
    await client.post(f"/api/tasks/{tid}/complete")
    with psycopg.connect(db) as other:  # a different session: only committed data is visible
        assert other.execute("select completed_at is not null from daily_tasks where id=%s", (tid,)).fetchone()[0]


async def test_concurrent_double_click_awards_once(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["reading"]
    results = await asyncio.gather(*[client.post(f"/api/tasks/{tid}/complete") for _ in range(6)])
    assert all(r.status_code == 200 for r in results)
    assert sum(not r.json()["already_completed"] for r in results) == 1
    with psycopg.connect(db) as conn:
        assert conn.execute("select count(*) from skill_history").fetchone()[0] == 1


async def test_briefing_completes_when_all_three_done_and_errors(client, db):
    seed_articles()
    generate_today()
    ids = task_ids(db)
    flags = [(await client.post(f"/api/tasks/{ids['reading']}/complete")).json()["briefing_completed"],
             (await client.post(f"/api/tasks/{ids['insight']}/complete")).json()["briefing_completed"]]
    assert flags == [False, False]
    r = await client.post(f"/api/tasks/{ids['hands_on']}/submit", json={"submission": GOOD_SUBMISSION})  # hands-on is completed by a PASS
    assert r.json()["briefing_completed"] is True
    assert (await client.post("/api/tasks/00000000-0000-0000-0000-000000000000/complete")).status_code == 404
    assert (await client.post("/api/tasks/not-a-uuid/complete")).status_code == 422


GOOD_SUBMISSION = "import numpy as np\n\ndef cosine(a, b):\n    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))\n\nprint(cosine([1, 0], [1, 1]))  # 0.7071067811865475"


async def level_of(client, slug):
    return {s["slug"]: s["level"] for s in (await client.get("/api/skills")).json()}[slug]


# --------------------------------------------------------------------------------------------- deep-learning mode
async def test_hands_on_cannot_be_ticked_off_it_requires_a_graded_submission(client, db):
    seed_articles()
    generate_today()
    r = await client.post(f"/api/tasks/{task_ids(db)['hands_on']}/complete")
    assert r.status_code == 409 and "submitting your work" in r.json()["detail"]
    assert await level_of(client, "rag") == 20  # untouched


async def test_task_detail_serves_chapter_project_rubric_and_essay_on_demand(client, db):
    seed_articles()
    generate_today()
    ids = task_ids(db)
    reading = (await client.get(f"/api/tasks/{ids['reading']}")).json()
    assert reading["reading"]["content"].startswith("## Why this exists") and reading["reading"]["word_count"] >= 1500
    assert reading["hands_on"] is None and reading["attempts_max"] == 6
    ho = (await client.get(f"/api/tasks/{ids['hands_on']}")).json()
    assert len(ho["hands_on"]["steps"]) == 4 and ho["hands_on"]["rubric"]["pass_score"] == 6 and ho["reading"] is None
    assert ho["submission"] is None and ho["evaluation"] is None
    ins = (await client.get(f"/api/tasks/{ids['insight']}")).json()
    assert ins["insight"]["word_count"] >= 450 and ins["insight"]["body"]
    assert (await client.get("/api/tasks/00000000-0000-0000-0000-000000000000")).status_code == 404


async def test_passing_submission_completes_the_task_and_moves_only_that_skill(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["hands_on"]
    before = {s["slug"]: s["level"] for s in (await client.get("/api/skills")).json()}
    r = await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION})
    assert r.status_code == 200
    out = r.json()
    assert out["passed"] is True and out["evaluation"]["score"] == 10 and out["task"]["completed"] and out["attempts_used"] == 1
    assert out["skill"]["slug"] == "rag" and out["skill"]["delta"] > 0
    after = {s["slug"]: s["level"] for s in (await client.get("/api/skills")).json()}
    assert after["rag"] == pytest.approx(before["rag"] + out["skill"]["delta"], abs=0.01)
    assert all(after[k] == before[k] for k in before if k != "rag")
    detail = (await client.get(f"/api/tasks/{tid}")).json()
    assert detail["submission"] == GOOD_SUBMISSION and detail["evaluation"]["passed"] and detail["task"]["evaluation_score"] == 10
    again = await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION})
    assert again.status_code == 409 and "already passed" in again.json()["detail"]


async def test_failing_submission_gives_feedback_but_never_moves_the_skill(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["hands_on"]
    bad = "FAILME " + GOOD_SUBMISSION
    r1 = (await client.post(f"/api/tasks/{tid}/submit", json={"submission": bad})).json()
    assert r1["passed"] is False and r1["evaluation"]["score"] == 1 and r1["skill"] is None and not r1["task"]["completed"]
    assert r1["evaluation"]["problems"] and r1["attempts_used"] == 1
    assert await level_of(client, "rag") == 20
    with psycopg.connect(db) as conn:
        assert conn.execute("select count(*) from skill_history").fetchone()[0] == 0
        row = conn.execute("select user_submission, ai_evaluation_feedback, evaluation_passed, attempt_count from daily_tasks where id=%s", (tid,)).fetchone()
        assert row[0] == bad and "What you did well" in row[1] and row[2] is False and row[3] == 1
    r2 = (await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION})).json()  # retry succeeds
    assert r2["passed"] and r2["attempts_used"] == 2 and r2["skill"]["delta"] > 0


async def test_attempt_cap_validation_and_wrong_task_kinds(client, db, monkeypatch):
    seed_articles()
    generate_today()
    ids = task_ids(db)
    monkeypatch.setenv("MAX_SUBMISSION_ATTEMPTS", "2")
    get_settings.cache_clear()
    bad = "FAILME " + GOOD_SUBMISSION
    for _ in range(2):
        assert (await client.post(f"/api/tasks/{ids['hands_on']}/submit", json={"submission": bad})).status_code == 200
    capped = await client.post(f"/api/tasks/{ids['hands_on']}/submit", json={"submission": bad})
    assert capped.status_code == 429 and "attempt limit" in capped.json()["detail"]
    assert (await client.post(f"/api/tasks/{ids['hands_on']}/submit", json={"submission": "too short"})).status_code == 422
    assert (await client.post(f"/api/tasks/{ids['reading']}/submit", json={"submission": GOOD_SUBMISSION})).status_code == 409
    assert (await client.post("/api/tasks/00000000-0000-0000-0000-000000000000/submit", json={"submission": GOOD_SUBMISSION})).status_code == 404
    monkeypatch.setenv("MAX_SUBMISSION_CHARS", "50")
    get_settings.cache_clear()
    assert (await client.post(f"/api/tasks/{ids['hands_on']}/submit", json={"submission": GOOD_SUBMISSION})).status_code == 413


async def test_grader_failure_refunds_the_attempt_and_budget_exhaustion_is_a_503(client, db):
    from app.curriculum.errors import LLMBudgetExceeded, LLMValidationFailed
    seed_articles()
    generate_today()
    tid = task_ids(db)["hands_on"]

    class Broken:
        def __init__(self, exc): self.exc = exc
        def generate(self, **kw): raise self.exc

    client.app.dependency_overrides[get_llm] = lambda: Broken(LLMValidationFailed("grader kept disagreeing with the rubric"))
    r = await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION})
    assert r.status_code == 502 and "not counted" in r.json()["detail"]
    client.app.dependency_overrides[get_llm] = lambda: Broken(LLMBudgetExceeded("cap is $1.00"))
    r = await client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION})
    assert r.status_code == 503 and "budget" in r.json()["detail"].lower()
    with psycopg.connect(db) as conn:
        assert conn.execute("select attempt_count, evaluation_result from daily_tasks where id=%s", (tid,)).fetchone() == (0, None)
    assert await level_of(client, "rag") == 20


async def test_simultaneous_passing_submissions_award_the_skill_once(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["hands_on"]
    results = await asyncio.gather(*[client.post(f"/api/tasks/{tid}/submit", json={"submission": GOOD_SUBMISSION}) for _ in range(3)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}
    with psycopg.connect(db) as conn:
        assert conn.execute("select count(*) from skill_history").fetchone()[0] == 1


# --------------------------------------------------------------------------------------------- feed
def with_skill_embeddings(db):
    from app.db import get_pool  # the app's pool sets search_path + registers the vector type

    with get_pool().connection() as conn:
        for r in conn.execute("select slug from skills").fetchall():
            conn.execute("update skills set embedding=%s where slug=%s", (unit_vec(rng), r["slug"]))


async def test_feed_keyset_pagination_walks_every_article_once(client, db):
    seed_articles(24)
    seen, cursor = [], None
    for _ in range(10):
        r = (await client.get("/api/feed", params={"limit": 5, **({"cursor": cursor} if cursor else {})})).json()
        seen += [i["id"] for i in r["items"]]
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 24 == len(set(seen))
    assert r["items"][-1]["skill_match"] is None  # no skill embeddings yet -> no match, still served


async def test_feed_hides_duplicates_and_reports_also_covered_by(client, db):
    vecs = seed_articles(4, per_source=2)
    states = irepo.load_source_states()
    with psycopg.connect(db) as conn:
        canon = str(conn.execute("select id from articles where url_hash='hash0'").fetchone()[0])
    dup = RawItem(title="Same story elsewhere", url="https://other.test/x", published_at=datetime.now(timezone.utc))
    irepo.insert_article(states["s1"].id, dup, dup.url, "dup1", vecs[0].tolist(), canon, 0.97)
    items = (await client.get("/api/feed")).json()["items"]
    assert len(items) == 4 and "Same story elsewhere" not in [i["title"] for i in items]
    assert next(i for i in items if i["id"] == canon)["also_covered_by"] == ["Source 1"]


async def test_feed_category_skill_and_relevance_sort(client, db):
    seed_articles(24)
    with_skill_embeddings(db)
    all_items = (await client.get("/api/feed", params={"limit": 50})).json()["items"]
    assert all(i["skill_match"] and i["skill_match"]["strength"] in ("strong", "moderate", "weak") for i in all_items)
    assert (await client.get("/api/feed", params={"category": "research"})).json()["items"] == []
    assert len((await client.get("/api/feed", params={"category": "engineering_blog", "limit": 50})).json()["items"]) == 24
    slug = all_items[0]["skill_match"]["skill_slug"]
    only = (await client.get("/api/feed", params={"skill": slug, "limit": 50})).json()["items"]
    assert only and all(i["skill_match"]["skill_slug"] == slug for i in only)
    rel = []
    cursor = None
    while True:
        r = (await client.get("/api/feed", params={"sort": "relevance", "limit": 7, **({"cursor": cursor} if cursor else {})})).json()
        rel += [i["relevance"] for i in r["items"]]
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert len(rel) == 24 and rel == sorted(rel, reverse=True)


async def test_feed_validation(client):
    assert (await client.get("/api/feed", params={"limit": 500})).status_code == 422
    assert (await client.get("/api/feed", params={"category": "memes"})).status_code == 422
    assert (await client.get("/api/feed", params={"cursor": "garbage"})).status_code == 400
    ok = (await client.get("/api/feed", params={"sort": "relevance"}))
    assert ok.status_code == 200  # empty db is a valid, empty page
    assert ok.json() == {"items": [], "next_cursor": None}


# --------------------------------------------------------------------------------------------- ops / security
async def test_health_cors_and_auth(client, db, monkeypatch):
    assert (await client.get("/health")).json() == {"status": "ok"}
    pre = await client.options("/api/today", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert pre.headers["access-control-allow-origin"] == "http://localhost:3000"
    bad = await client.options("/api/today", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers

    monkeypatch.setenv("API_TOKEN", "s3cret")
    get_settings.cache_clear()
    assert (await client.get("/api/skills")).status_code == 401
    assert (await client.get("/api/skills", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.get("/api/skills", headers={"Authorization": "Bearer s3cret"})).status_code == 200
    assert (await client.get("/health")).status_code == 200  # not behind the token


async def test_generate_endpoint_queues_only_when_needed(client, db):
    from app.api.deps import get_enqueue

    calls = []
    client.app.dependency_overrides[get_enqueue] = lambda: calls.append
    r = await client.post("/api/briefing/generate")
    assert r.status_code == 202 and r.json() == {"queued": True, "reason": None, "run_id": None} and calls == ["app.tasks.generate_daily_briefing"]

    def broken(name):
        raise ConnectionError("redis down")
    client.app.dependency_overrides[get_enqueue] = lambda: broken
    assert (await client.post("/api/briefing/generate")).status_code == 503

    seed_articles()
    generate_today()
    client.app.dependency_overrides[get_enqueue] = lambda: calls.append
    assert (await client.post("/api/briefing/generate")).json()["reason"] == "already generated"
    assert len(calls) == 1


# --------------------------------------------------------------------------------------------- summaries
class FakeSummaryLLM:
    def generate(self, *, system, user, output_model, grounding, model=None, temperature=None):
        assert output_model is SummaryBatch and model == "gpt-4o-mini" and temperature == 0.2 and "untrusted" in system
        return output_model.model_validate({"summaries": [{"id": i, "summary": f"Summary for {i}."} for i in sorted(grounding.article_ids)]}, context=grounding)


async def test_summaries_fill_ai_summary_and_feed_serves_it(client, db):
    seed_articles(12)
    s = Settings(summary_batch_size=5)
    out = summaries.summarize_pending(llm=FakeSummaryLLM(), settings=s)
    assert out == {"summarized": 12, "failed": 0, "pending_seen": 12}
    assert summaries.summarize_pending(llm=FakeSummaryLLM(), settings=s)["pending_seen"] == 0  # idempotent
    items = (await client.get("/api/feed", params={"limit": 50})).json()["items"]
    assert all(i["ai_summary"] and i["ai_summary"].startswith("Summary for S") for i in items)


def test_summary_schema_requires_exact_coverage():
    from app.curriculum.schemas import GroundingContext
    ctx = GroundingContext(frozenset({"S1", "S2"}), frozenset())
    with pytest.raises(Exception, match="exactly one summary"):
        SummaryBatch.model_validate({"summaries": [{"id": "S1", "summary": "x"}]}, context=ctx)
    with pytest.raises(Exception, match="https"):
        SummaryBatch.model_validate({"summaries": [{"id": "S1", "summary": "see https://x.y"}, {"id": "S2", "summary": "y"}]}, context=ctx)


# --------------------------------------------------------------------------------------------- history / by-date
def _insert_past_briefing(db, day):
    """Clone today's stored briefing onto an earlier date (day is unique per briefing)."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("update daily_briefings set day = %s", (day,))


async def test_history_is_empty_then_lists_generated_days_as_plain_strings(client, db):
    assert (await client.get("/api/briefings/history")).json() == []
    seed_articles()
    generate_today()
    r = await client.get("/api/briefings/history")
    assert r.status_code == 200 and r.json() == [TODAY.isoformat()]


async def test_briefing_by_date_today_matches_api_today_and_pending_is_200(client, db):
    pending = await client.get(f"/api/briefings/{TODAY}")
    assert pending.status_code == 200 and pending.json()["state"] == "pending"
    seed_articles()
    generate_today()
    assert (await client.get(f"/api/briefings/{TODAY}")).json() == (await client.get("/api/today")).json()


async def test_briefing_by_date_serves_stored_past_day_and_404s_missing_future_and_bad_dates(client, db):
    from datetime import timedelta
    seed_articles()
    generate_today()
    past = TODAY - timedelta(days=3)
    _insert_past_briefing(db, past)
    r = await client.get(f"/api/briefings/{past}")
    assert r.status_code == 200 and r.json()["state"] == "ready" and len(r.json()["briefing"]["tasks"]) == 3
    assert (await client.get(f"/api/briefings/{past - timedelta(days=1)}")).status_code == 404
    assert (await client.get(f"/api/briefings/{TODAY + timedelta(days=1)}")).status_code == 404
    assert (await client.get("/api/briefings/not-a-date")).status_code == 422


async def test_task_detail_marks_past_day_tasks_archived(client, db):
    from datetime import timedelta
    seed_articles()
    generate_today()
    ids = task_ids(db)
    assert (await client.get(f"/api/tasks/{ids['hands_on']}")).json()["archived"] is False
    _insert_past_briefing(db, TODAY - timedelta(days=2))
    assert (await client.get(f"/api/tasks/{ids['hands_on']}")).json()["archived"] is True


async def test_notes_roundtrip_overwrite_limits_and_task_detail(client, db):
    seed_articles()
    generate_today()
    tid = task_ids(db)["reading"]
    assert (await client.get(f"/api/tasks/{tid}/notes")).json() == {"notes": "", "updated_at": None}
    r = await client.put(f"/api/tasks/{tid}/notes", json={"notes": "softmax = exp / sum\nline 2 \u00e9"})
    assert r.status_code == 200 and r.json()["updated_at"]
    assert (await client.get(f"/api/tasks/{tid}/notes")).json()["notes"] == "softmax = exp / sum\nline 2 \u00e9"
    assert (await client.get(f"/api/tasks/{tid}")).json()["notes"].startswith("softmax")
    await client.put(f"/api/tasks/{tid}/notes", json={"notes": ""})
    assert (await client.get(f"/api/tasks/{tid}/notes")).json()["notes"] == ""
    assert (await client.put(f"/api/tasks/{tid}/notes", json={"notes": "x" * 50001})).status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/tasks/{missing}/notes")).status_code == 404
    assert (await client.put(f"/api/tasks/{missing}/notes", json={"notes": "a"})).status_code == 404
