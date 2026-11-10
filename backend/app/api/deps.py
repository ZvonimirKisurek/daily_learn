"""FastAPI dependencies: settings, the async DB pool, per-request connections, auth, task queue.

WHY ASYNC?  FastAPI runs on an event loop: one thread juggles many in-flight requests. A request that awaits
the database *yields* the thread, so while Postgres works on query A the loop serves request B. A blocking
(sync) driver would freeze the loop for every other client for the duration of each query. `psycopg`'s
AsyncConnectionPool gives us true non-blocking I/O. (Celery workers and the CLI keep using the sync pool in
app/db.py: they are single-job processes where blocking is fine.)

CONNECTION LIFECYCLE: one pool per process (opened/closed in `lifespan`), one *borrowed connection per request*.
`async with pool.connection()` is also a transaction: it COMMITs when the block exits cleanly and ROLLBACKs on an
exception - so a failing endpoint can never leave half-applied writes.
"""
from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pgvector.psycopg import register_vector_async

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


async def _configure(conn: AsyncConnection) -> None:
    await conn.execute("SET search_path TO public, extensions")  # Supabase keeps `vector` in `extensions`
    await conn.commit()
    await register_vector_async(conn)


def build_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        settings.database_url, min_size=1, max_size=settings.db_pool_size, open=False, configure=_configure,
        # prepare_threshold=None keeps us compatible with PgBouncer/Supavisor transaction pooling.
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
    )


def settings_dep() -> Settings:  # a function (not a constant) so tests can override it via app.dependency_overrides
    return get_settings()


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


async def get_conn(pool: Annotated[AsyncConnectionPool, Depends(get_pool)]) -> AsyncIterator[AsyncConnection]:
    async with pool.connection() as conn:
        yield conn


# scope="function": the dependency's teardown (= COMMIT) runs right after the endpoint function returns and
# BEFORE the response is sent. With the default scope, newer FastAPI versions run teardown after sending, so a
# client could receive "200 OK" and immediately read data that is not committed yet (read-after-write race).
Conn = Annotated[AsyncConnection, Depends(get_conn, scope="function")]
Cfg = Annotated[Settings, Depends(settings_dep)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


async def require_token(settings: Cfg, authorization: Annotated[str | None, Header()] = None) -> None:
    """Single-user bearer auth. The browser never sees this token: Next.js calls the API from the server."""
    if not settings.api_token:
        return  # open (development). Set API_TOKEN in production.
    scheme, _, token = (authorization or "").partition(" ")
    # API_TOKEN may hold several comma-separated tokens ("new,old") so a rotation has no downtime: add the new one,
    # switch the frontend over, then remove the old one. Every candidate is compared (no early exit).
    valid = [t.strip() for t in settings.api_token.split(",") if t.strip()]
    ok = False
    for t in valid:
        ok |= secrets.compare_digest(token.encode(), t.encode())
    if scheme.lower() != "bearer" or not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"})


_inline_lock = threading.Lock()


class QueueUnavailable(RuntimeError):
    """Neither the task queue nor the inline fallback can take the job."""


def _enqueue_inline(task_name: str, **kwargs) -> None:
    """Run the job in a daemon thread of this process (TASK_BACKEND=inline; no Redis/Celery needed).
    Returns immediately, like a queue would; the lock stops a double-click from running two LLM jobs at once."""
    def run() -> None:
        if not _inline_lock.acquire(blocking=False):
            log.info("inline job already running; ignoring %s", task_name)
            if kwargs.get("run_id"):  # don't leave the dashboard waiting on a run that will never start
                from app.bitcoin.predictor import _mark_run

                _mark_run(kwargs["run_id"], status="failed", error="Another background job was already running. Try again in a minute.")
            return
        try:
            if task_name == "app.tasks.generate_daily_briefing":
                from app.curriculum.generators import generate_daily_briefing as job
            elif task_name == "app.tasks.generate_weekly_theme":
                from app.curriculum.generators import generate_weekly_theme as job
            elif task_name == "app.tasks.generate_bitcoin_daily_insight":
                from functools import partial

                from app.bitcoin.predictor import generate_prediction

                job = partial(generate_prediction, trigger="manual", run_id=kwargs.get("run_id"))
            else:
                raise ValueError(f"unknown task {task_name}")
            job()
        except Exception:
            log.exception("inline job %s failed", task_name)
        finally:
            _inline_lock.release()

    threading.Thread(target=run, name=f"inline-{task_name}", daemon=True).start()


def _enqueue_celery(task_name: str, **kwargs) -> None:
    from app.celery_app import app as celery

    # Pre-flight: kombu retries a dead broker connection for a long time by default (~20s). Probe once with a
    # short timeout so a missing Redis surfaces as a quick 503, not a hung request.
    with celery.connection_for_write() as conn:
        conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0.2, timeout=2)
    celery.send_task(task_name, kwargs=kwargs or None, retry=False)


def get_llm(settings: Cfg):
    """The grader. A dependency so tests and the demo server can substitute a fake without touching OpenAI."""
    from app.curriculum.llm import make_llm

    return make_llm(settings)


def get_enqueue(settings: Cfg) -> Callable[..., None]:
    """The function the API uses to start background work, chosen by TASK_BACKEND.

    With Celery, a dead broker (Redis down, wrong REDIS_URL, Upstash quota exhausted) used to surface as a bare
    "task queue unavailable". Now it falls back to the inline runner (TASK_INLINE_FALLBACK, default on) so a button press
    still works on a single-user deployment, or raises QueueUnavailable with a message the UI can show."""
    if settings.task_backend == "inline":
        return _enqueue_inline
    fallback = settings.task_inline_fallback

    def enqueue(task_name: str, **kwargs) -> None:
        try:
            _enqueue_celery(task_name, **kwargs)
        except Exception as exc:  # noqa: BLE001 - any broker failure
            if not fallback:
                raise QueueUnavailable("Analysis engine offline: the task queue (Redis) is unreachable. Please try again later.") from exc
            log.warning("task queue unreachable (%r); running %s inline", exc, task_name)
            _enqueue_inline(task_name, **kwargs)

    return enqueue
