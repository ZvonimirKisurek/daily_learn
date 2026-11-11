"""Fetch -> Parse -> (exact-URL dedup) -> Embed -> (semantic dedup) -> Store."""
from __future__ import annotations

import asyncio
import logging

import httpx
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.ingestion import repository as repo
from app.ingestion.embeddings import Embedder, OpenAIEmbedder, embedding_input
from app.ingestion.fetchers import FETCHERS
from app.ingestion.http import PoliteClient, RobotsDisallowed
from app.ingestion.models import RawItem, SourceOutcome, SourceSkipped, SourceSpec, SourceState
from app.ingestion.parsers import canonicalize_url, url_hash
from app.ingestion.sources import load_all_sources

log = logging.getLogger(__name__)


def classify_error(exc: Exception) -> str:
    """Bucket a fetch failure so `ingest --dry-run` can tell you *what kind* of fix a source needs."""
    if isinstance(exc, RobotsDisallowed):
        return "robots"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http"
    if isinstance(exc, httpx.TransportError):
        return "network"
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):  # bad XML/JSON/HTML shape
        return "parse"
    return "other"


def breaker_open(state: SourceState, settings: Settings, now: datetime | None = None) -> bool:
    """Circuit breaker: a source failing repeatedly is only retried once per cooldown window."""
    if state.consecutive_failures < settings.breaker_failures or state.last_fetched_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - state.last_fetched_at < timedelta(hours=settings.breaker_cooldown_hours)


async def fetch_all(
    specs: list[SourceSpec], states: dict[str, SourceState], settings: Settings, client: PoliteClient | None = None,
) -> list[SourceOutcome]:
    own_client = client is None
    client = client or PoliteClient(
        settings.user_agent, timeout=settings.http_timeout, max_retries=settings.http_max_retries,
        min_interval=settings.host_min_interval, host_intervals=settings.host_intervals,
    )
    sem = asyncio.Semaphore(settings.fetch_concurrency)

    async def one(spec: SourceSpec) -> SourceOutcome:
        state = states.get(spec.slug, SourceState())
        out = SourceOutcome(spec, state)
        if not spec.enabled:
            out.skipped = "disabled"
        elif breaker_open(state, settings):
            out.skipped = "circuit breaker open"
        else:
            async with sem:
                try:
                    out.result = await FETCHERS[spec.kind](spec, state, client, settings)
                except SourceSkipped as exc:
                    out.skipped = str(exc)
                except Exception as exc:  # isolate: one broken source must never sink the run
                    out.error = f"{exc.__class__.__name__}: {exc}"
                    out.error_kind = classify_error(exc)
        return out

    try:
        return await asyncio.gather(*(one(s) for s in specs))
    finally:
        if own_client:
            await client.aclose()


def _prepare(outcomes: list[SourceOutcome], settings: Settings) -> list[tuple[SourceOutcome, RawItem, str, str]]:
    """Canonicalise, drop stale / in-batch-duplicate / already-stored URLs."""
    oldest = datetime.now(timezone.utc) - timedelta(days=settings.max_item_age_days)
    cand: list[tuple[SourceOutcome, RawItem, str, str]] = []
    seen: set[str] = set()
    for o in outcomes:
        for item in (o.result.items if o.result else []):
            if item.published_at and item.published_at < oldest:
                continue
            url = canonicalize_url(item.url)
            h = url_hash(url)
            if h not in seen:
                seen.add(h)
                cand.append((o, item, url, h))
    known = repo.existing_url_hashes([c[3] for c in cand])
    return [c for c in cand if c[3] not in known]


def store(outcomes: list[SourceOutcome], settings: Settings, embedder: Embedder) -> Counter:
    stats: Counter = Counter()
    fresh = _prepare(outcomes, settings)
    stats["new_urls"] = len(fresh)
    if fresh:
        try:
            vectors = embedder.embed([embedding_input(i.title, i.summary) for _, i, _, _ in fresh])
        except Exception:
            # Nothing was stored, so every URL stays "new" and is retried on the next run.
            log.exception("embedding failed; %d items deferred", len(fresh))
            stats["embed_failures"] = len(fresh)
            fresh, vectors = [], []
        # Sequential on purpose: each insert is visible to the next similarity lookup, so two
        # near-identical stories arriving in the same batch are also collapsed.
        for (o, item, url, h), vec in zip(fresh, vectors):
            near = repo.find_nearest(vec, window_days=settings.dedup_window_days)
            is_dup = near is not None and near[1] >= settings.dedup_threshold
            # A duplicate's vector is never queried again (the HNSW index and the feed only use canonical
            # articles), and a 1536-float vector is ~6 KB, so we don't store it: it keeps the database small.
            new_id = repo.insert_article(
                o.state.id, item, url, h, None if is_dup else vec,
                duplicate_of=near[0] if is_dup else None, duplicate_sim=near[1] if near else None,
            )
            if new_id:
                stats["duplicates" if is_dup else "inserted"] += 1
    for o in outcomes:
        if o.state.id and not o.skipped:
            repo.record_source_result(
                o.state.id, error=o.error,
                etag=o.result.etag if o.result else None, last_modified=o.result.last_modified if o.result else None,
            )
    return stats


def run_ingestion(
    *, trigger: str = "manual", only: list[str] | None = None, embedder: Embedder | None = None,
    settings: Settings | None = None, track: str | None = None,
) -> dict:
    settings = settings or get_settings()
    specs = load_all_sources(settings)
    repo.upsert_sources(specs)
    if only:
        specs = [s for s in specs if s.slug in only]
    if track:
        specs = [s for s in specs if s.track == track]
    states = repo.load_source_states()
    run_id = repo.start_run(trigger)
    try:
        outcomes = asyncio.run(fetch_all(specs, states, settings))
        stats = store(outcomes, settings, embedder or OpenAIEmbedder(settings))
        stats.update(
            sources_ok=sum(1 for o in outcomes if o.result), sources_failed=sum(1 for o in outcomes if o.error),
            sources_skipped=sum(1 for o in outcomes if o.skipped),
        )
        status = "success" if not stats["sources_failed"] and not stats["embed_failures"] else "partial"
        repo.finish_run(run_id, status, dict(stats))
        log.info("ingestion %s: %s", status, dict(stats))
        return dict(stats)
    except Exception as exc:
        repo.finish_run(run_id, "failed", {"error": str(exc)[:500]})
        raise
