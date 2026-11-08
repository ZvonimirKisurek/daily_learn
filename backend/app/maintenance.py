"""Retention. Each canonical article costs ~12 KB (1536 float32 = 6 KB for the vector, plus its HNSW index
entry). At a few hundred new articles a day that reaches a 500 MB free database in a few months, so old rows go.

Invariant: `article_days` must exceed `max_item_age_days` (30). Ingestion refuses items older than that, so a feed
that still lists a pruned article cannot resurrect it as "new".
"""
from __future__ import annotations

from app.config import Settings, get_settings
from app.db import get_pool


def prune(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    article_days = max(s.retention_days, s.max_item_age_days + 1)
    with get_pool().connection() as conn:
        # Old canonical articles go together with their duplicates (otherwise `duplicate_of` would be nulled by the
        # FK and the orphans would show up in the feed as if they were canonical).
        old = conn.execute(
            """delete from articles where id in (select id from articles where duplicate_of is null
                   and coalesce(published_at, fetched_at) < now() - make_interval(days => %(d)s))
               or duplicate_of in (select id from articles where duplicate_of is null
                   and coalesce(published_at, fetched_at) < now() - make_interval(days => %(d)s))""",
            {"d": article_days}).rowcount
        dups = conn.execute(
            "delete from articles where duplicate_of is not null and fetched_at < now() - make_interval(days => %s)",
            (s.duplicate_retention_days,)).rowcount
        runs = conn.execute("delete from ingestion_runs where started_at < now() - interval '30 days'").rowcount
        # Bitcoin news (Phase 11). Prices and predictions are kept: they are tiny and are the record you score accuracy against.
        btc_news = conn.execute("delete from bitcoin_news where fetched_at < now() - make_interval(days => %s)",
                                (s.bitcoin_news_retention_days,)).rowcount
    return {"articles_deleted": old, "stale_duplicates_deleted": dups, "ingestion_runs_deleted": runs, "bitcoin_news_deleted": btc_news}
