"""All SQL for the ingestion pipeline lives here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import numpy as np
from psycopg.types.json import Jsonb

from app.db import get_pool
from app.ingestion.models import RawItem, SourceSpec, SourceState


def upsert_sources(specs: Iterable[SourceSpec]) -> int:
    n = 0
    with get_pool().connection() as conn:
        for s in specs:
            conn.execute(
                """
                insert into sources (slug, name, category, kind, url, config, weight, enabled, track)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (slug) do update set
                  name=excluded.name, category=excluded.category, kind=excluded.kind, url=excluded.url,
                  config=excluded.config, weight=excluded.weight, enabled=excluded.enabled, track=excluded.track, updated_at=now()
                """,
                (s.slug, s.name, s.category, s.kind, s.url, Jsonb(s.config), s.weight, s.enabled, s.track),
            )
            n += 1
    return n


def load_source_states() -> dict[str, SourceState]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            "select id, slug, etag, last_modified, consecutive_failures, last_fetched_at from sources"
        ).fetchall()
    return {
        r["slug"]: SourceState(str(r["id"]), r["etag"], r["last_modified"], r["consecutive_failures"], r["last_fetched_at"])
        for r in rows
    }


def record_source_result(source_id: str, *, error: str | None, etag: str | None, last_modified: str | None) -> None:
    with get_pool().connection() as conn:
        if error is None:
            conn.execute(
                """update sources set last_fetched_at=now(), last_success_at=now(), last_error=null,
                   consecutive_failures=0, etag=%s, last_modified=%s where id=%s""",
                (etag, last_modified, source_id),
            )
        else:
            conn.execute(
                """update sources set last_fetched_at=now(), last_error=%s,
                   consecutive_failures=consecutive_failures+1 where id=%s""",
                (error[:500], source_id),
            )


def existing_url_hashes(hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    with get_pool().connection() as conn:
        rows = conn.execute("select url_hash from articles where url_hash = any(%s)", (hashes,)).fetchall()
    return {r["url_hash"] for r in rows}


def find_nearest(embedding: list[float], *, window_days: int, k: int = 5) -> tuple[str, float] | None:
    """Nearest canonical article by cosine similarity, restricted to recent ones.

    `<=>` is pgvector's cosine *distance* (0 = identical direction), so similarity = 1 - distance.
    The `duplicate_of is null` predicate matches the partial HNSW index, so this is an ANN lookup,
    not a table scan. We over-fetch k candidates and apply the time window afterwards, because
    filtering inside an HNSW scan can starve results.
    """
    vec = np.asarray(embedding, dtype=np.float32)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            select id, 1 - (embedding <=> %(v)s) as sim, coalesce(published_at, fetched_at) as ts
            from articles where duplicate_of is null and embedding is not null
            order by embedding <=> %(v)s limit %(k)s
            """,
            {"v": vec, "k": k},
        ).fetchall()
    for r in rows:
        if r["ts"] >= cutoff:
            return str(r["id"]), float(r["sim"])
    return None


def insert_article(
    source_id: str, item: RawItem, url: str, url_hash: str, embedding: list[float] | None,
    duplicate_of: str | None, duplicate_sim: float | None,
) -> str | None:
    """Returns the new id, or None if another worker inserted the same URL first."""
    with get_pool().connection() as conn:
        row = conn.execute(
            """
            insert into articles (source_id, url, url_hash, title, summary, author, published_at,
                                  embedding, duplicate_of, duplicate_sim, engagement, metadata)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (url_hash) do nothing returning id
            """,
            (source_id, url, url_hash, item.title, item.summary, item.author, item.published_at,
             None if embedding is None else np.asarray(embedding, dtype=np.float32), duplicate_of, duplicate_sim, item.engagement, Jsonb(item.metadata)),
        ).fetchone()
    return str(row["id"]) if row else None


def start_run(trigger: str) -> str:
    with get_pool().connection() as conn:
        return str(conn.execute("insert into ingestion_runs (trigger) values (%s) returning id", (trigger,)).fetchone()["id"])


def finish_run(run_id: str, status: str, stats: dict) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "update ingestion_runs set finished_at=now(), status=%s, stats=%s where id=%s",
            (status, Jsonb(stats), run_id),
        )
