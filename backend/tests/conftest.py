"""Integration fixtures: a throwaway embedded Postgres 16 + pgvector with the real migrations applied.
Skipped automatically if `pixeltable-pgserver` is not installed (it is in the dev extras)."""
import os
from pathlib import Path

import psycopg
import pytest

# Lessons are not generated on weekends by default (settings.weekend_lessons). The suite builds lessons for "today", so it
# must not depend on the day it runs: switch the rule off here; the tests of the rule itself pass weekend_lessons=False.
os.environ.setdefault("WEEKEND_LESSONS", "true")

MIGRATIONS = sorted((Path(__file__).parents[2] / "supabase" / "migrations").glob("*.sql"))


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory):
    pgserver = pytest.importorskip("pixeltable_pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        for m in MIGRATIONS:
            conn.execute(m.read_text())
    yield uri
    server.cleanup()


@pytest.fixture
def db(pg_uri):
    """Fresh data per test, pool pointed at the embedded server."""
    import app.db as appdb
    from app.config import get_settings

    os.environ["DATABASE_URL"] = pg_uri
    get_settings.cache_clear()
    if appdb._pool is not None:
        appdb._pool.close()
        appdb._pool = None
    with psycopg.connect(pg_uri, autocommit=True) as conn:
        conn.execute("truncate articles, sources, ingestion_runs, weekly_themes, daily_briefings, skill_history, llm_usage cascade")
        conn.execute("truncate exams, xp_events, level_completions, level_embeddings cascade")
        conn.execute("update learner_progress set current_level = 1, placement_taken_at = null")
        conn.execute("update sources set track = 'ai'")
        conn.execute("update skills set level = 20, embedding = null")
    yield pg_uri
    if appdb._pool is not None:
        appdb._pool.close()
        appdb._pool = None
