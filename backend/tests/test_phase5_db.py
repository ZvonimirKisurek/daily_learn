"""Phase 5 behaviours that need real SQL: retention, the Phase 1 store() pipeline, min_strength, roadmap in the API."""
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import psycopg
import pytest

from app.config import Settings, get_settings
from app.curriculum import generators
from app.ingestion import repository as irepo
from app.ingestion.models import FetchResult, RawItem, SourceOutcome, SourceSpec
from app.ingestion.pipeline import store
from app.maintenance import prune
from app.main import create_app
from tests.factories import unit_vec, weekly_dict
from tests.test_integration_db import FakeEmbedder, FakeLLM, seed_articles

pytestmark = pytest.mark.integration
rng = np.random.default_rng(11)


def count(db, sql):
    with psycopg.connect(db) as c:
        return c.execute(sql).fetchone()[0]


def test_prune_removes_old_articles_with_their_duplicates_and_keeps_recent(db):
    seed_articles(8, per_source=4)  # published 0..7 hours ago
    states = irepo.load_source_states()
    old = RawItem(title="old", url="https://o.test/1", published_at=datetime.now(timezone.utc) - timedelta(days=90))
    old_id = irepo.insert_article(states["s0"].id, old, old.url, "old1", unit_vec(rng).tolist(), None, None)
    dup = RawItem(title="old dup", url="https://o.test/2", published_at=datetime.now(timezone.utc) - timedelta(days=89))
    irepo.insert_article(states["s0"].id, dup, dup.url, "old2", None, old_id, 0.97)
    fresh_dup = RawItem(title="fresh dup", url="https://o.test/3", published_at=datetime.now(timezone.utc))
    with psycopg.connect(db) as c:
        keep_id = str(c.execute("select id from articles where url_hash='hash0'").fetchone()[0])
        c.execute("update articles set fetched_at = now() - interval '30 days' where url_hash='old2'")
    irepo.insert_article(states["s0"].id, fresh_dup, fresh_dup.url, "fresh1", None, keep_id, 0.95)

    out = prune(Settings())
    assert out["articles_deleted"] == 2 and out["stale_duplicates_deleted"] == 0
    assert count(db, "select count(*) from articles where url_hash in ('old1','old2')") == 0
    assert count(db, "select count(*) from articles") == 8 + 1  # 8 recent canonical + the fresh duplicate


def test_prune_deletes_stale_duplicates_but_never_resurrects_orphans(db):
    seed_articles(4, per_source=4)
    states = irepo.load_source_states()
    with psycopg.connect(db) as c:
        keep_id = str(c.execute("select id from articles where url_hash='hash0'").fetchone()[0])
    d = RawItem(title="dup", url="https://o.test/9")
    irepo.insert_article(states["s0"].id, d, d.url, "dupx", None, keep_id, 0.95)
    with psycopg.connect(db) as c:
        c.execute("update articles set fetched_at = now() - interval '10 days' where url_hash='dupx'")
    assert prune(Settings(duplicate_retention_days=7))["stale_duplicates_deleted"] == 1
    assert count(db, "select count(*) from articles where duplicate_of is not null") == 0


def test_store_pipeline_dedups_semantically_and_skips_duplicate_vectors(db):
    """Phase 1's store(): URL dedup, embedding, nearest-neighbour dedup, insert - against real pgvector."""
    spec = SourceSpec(slug="s", name="S", category="research", url="https://s.test/feed")
    irepo.upsert_sources([spec])
    state = irepo.load_source_states()["s"]
    base = unit_vec(rng)
    vecs = {"Original story": base, "Same story, other words": base + 0.01 * unit_vec(rng), "Unrelated": unit_vec(rng)}

    class Embedder:
        def embed(self, texts):
            return [vecs[t.split("\n")[0]].tolist() for t in texts]

    now = datetime.now(timezone.utc)
    items = [RawItem(title=t, url=f"https://s.test/{i}", summary="x", published_at=now) for i, t in enumerate(vecs)]
    items.append(RawItem(title="Original story", url="https://www.s.test/0/?utm_source=x", published_at=now))  # same URL after canonicalisation
    outcome = SourceOutcome(spec, state, FetchResult(items=items))
    stats = store([outcome], Settings(), Embedder())
    assert stats["new_urls"] == 3 and stats["inserted"] == 2 and stats["duplicates"] == 1
    assert count(db, "select count(*) from articles where duplicate_of is not null and embedding is null") == 1
    assert count(db, "select duplicate_sim from articles where duplicate_of is not null") > 0.9
    assert store([outcome], Settings(), Embedder())["new_urls"] == 0  # second run: nothing new


@pytest.fixture
async def client(db, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")  # an empty env var beats a token in a local .env file (delenv would not)
    get_settings.cache_clear()
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c
    get_settings.cache_clear()


async def test_min_strength_filter_is_a_precision_knob(client, db):
    seed_articles(24)
    from app.db import get_pool
    with get_pool().connection() as conn:
        for r in conn.execute("select slug from skills").fetchall():
            conn.execute("update skills set embedding=%s where slug=%s", (unit_vec(rng), r["slug"]))
    everything = (await client.get("/api/feed", params={"limit": 50})).json()["items"]
    sims = sorted(i["skill_match"]["similarity"] for i in everything)
    strong_cut = sims[len(sims) // 2]
    get_settings.cache_clear()
    import os
    os.environ["MATCH_STRONG"], os.environ["MATCH_MODERATE"] = str(strong_cut), str(sims[2])
    get_settings.cache_clear()
    try:
        strong = (await client.get("/api/feed", params={"limit": 50, "min_strength": "strong"})).json()["items"]
        moderate = (await client.get("/api/feed", params={"limit": 50, "min_strength": "moderate"})).json()["items"]
    finally:
        del os.environ["MATCH_STRONG"], os.environ["MATCH_MODERATE"]
        get_settings.cache_clear()
    assert 0 < len(strong) < len(moderate) < len(everything)
    assert all(i["skill_match"]["strength"] == "strong" for i in strong)
    assert (await client.get("/api/feed", params={"min_strength": "weak"})).status_code == 422


async def test_roadmap_week_steers_prompt_and_is_exposed_by_the_api(client, db):
    seed_articles()
    s = Settings(timezone="UTC", min_candidates=10, roadmap_start="2026-10-26", roadmap_file="curriculum/roadmap.yaml")
    dry = generators.generate_weekly_theme(week_start=date(2026, 10, 26), dry_run=True, embedder=FakeEmbedder(), settings=s)
    assert "<roadmap_week" in dry["user"] and "Retrieval engineering" in dry["user"] and "Roadmap mode" in dry["system"]
    assert "<roadmap_week" not in generators.generate_weekly_theme(
        week_start=date(2026, 11, 30), dry_run=True, embedder=FakeEmbedder(), settings=s)["user"]  # past the 4 weeks => free mode

    seen = {}

    class RoadmapLLM(FakeLLM):
        def generate(self, *, system, user, output_model, grounding, **kw):
            seen["required"] = grounding.required_focus
            ids = sorted(grounding.article_ids, key=lambda x: int(x[1:]))
            return output_model.model_validate(weekly_dict(ids, skills=tuple(sorted(grounding.required_focus))), context=grounding)

    generators.generate_weekly_theme(week_start=date(2026, 10, 26), llm=RoadmapLLM(), embedder=FakeEmbedder(), settings=s)
    assert seen["required"] == frozenset({"rag", "vector-db", "llm-evals"})
    from app.curriculum.generators import monday_of
    today = datetime.now(timezone.utc).date()
    with psycopg.connect(db) as c:  # the API serves the theme for *this* week
        c.execute("update weekly_themes set week_start = %s", (monday_of(today),))
    generators.generate_daily_briefing(day=today, llm=FakeLLM(), embedder=FakeEmbedder(), settings=Settings(min_candidates=10))
    body = (await client.get("/api/today")).json()
    assert body["weekly_theme"]["theme"]["roadmap"]["index"] == 1 and body["weekly_theme"]["theme"]["roadmap"]["of"] == 4
