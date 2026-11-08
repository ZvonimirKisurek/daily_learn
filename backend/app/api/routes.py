from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import openai
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api import queries
from app.api.deps import Cfg, Conn, Pool, get_enqueue, get_llm, require_token
from app.api.schemas import BitcoinLatestOut, CompletionOut, NotesIn, NotesOut, FeedPage, QueueOut, SkillOut, SubmissionIn, SubmissionOut, TaskDetailOut, TodayOut
from app.curriculum.errors import LLMBudgetExceeded, LLMError
from app.curriculum.evaluation import evaluate
from app.ingestion.models import Category

# Auth applies to the whole router; /health lives outside it.
router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _today(cfg) -> "datetime.date":
    return datetime.now(ZoneInfo(cfg.timezone)).date()


@router.get("/today", response_model=TodayOut)
async def today(conn: Conn, cfg: Cfg):
    """Everything the Command Center needs in one round trip. `state="pending"` (briefing null) is a normal,
    expected response before 06:30 or if generation failed - it is NOT an error, so it is a 200."""
    return await queries.fetch_today(conn, _today(cfg))


@router.get("/briefings/history", response_model=list[date])
async def briefing_history(conn: Conn):
    """Every date (YYYY-MM-DD, oldest first) that has a generated briefing. The calendar uses it to know which days are clickable.
    Declared BEFORE /briefings/{day}: FastAPI matches routes in registration order, and 'history' must not be parsed as a date."""
    return await queries.list_briefing_days(conn)


@router.get("/briefings/{day}", response_model=TodayOut)
async def briefing_for_day(day: date, conn: Conn, cfg: Cfg):
    """One day's briefing, same shape as /api/today. Today behaves exactly like /api/today (a missing briefing is a 200 'pending').
    Any other day must have a stored briefing: past-without-data and future dates are a 404, so the UI never offers an empty page."""
    if day != _today(cfg) and not await queries.briefing_exists(conn, day):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no briefing for that date")
    return await queries.fetch_today(conn, day)


@router.post("/tasks/{task_id}/complete", response_model=CompletionOut)
async def complete_task(task_id: UUID, conn: Conn):
    """Idempotent: completing twice returns `already_completed: true` and awards nothing the second time."""
    try:
        result = await queries.complete_task(conn, task_id)
    except queries.SubmissionRequired:
        raise HTTPException(status.HTTP_409_CONFLICT, "hands-on tasks are completed by submitting your work for evaluation") from None
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return result


@router.get("/tasks/{task_id}", response_model=TaskDetailOut)
async def task_detail(task_id: UUID, conn: Conn, cfg: Cfg):
    """Everything a task page needs: the chapter, or the project + rubric + your last submission and feedback."""
    detail = await queries.get_task_detail(conn, task_id, cfg.max_submission_attempts)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    detail["archived"] = detail["day"] < _today(cfg)  # a past day's lab is read-only in the UI
    return detail


@router.get("/tasks/{task_id}/notes", response_model=NotesOut)
async def get_task_notes(task_id: UUID, conn: Conn):
    row = await queries.get_notes(conn, task_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return row


@router.put("/tasks/{task_id}/notes", response_model=NotesOut)
async def put_task_notes(task_id: UUID, body: NotesIn, conn: Conn):
    """Replace the notebook text. Idempotent, so the UI can safely auto-save (debounced) and retry."""
    row = await queries.save_notes(conn, task_id, body.notes)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return row


@router.post("/tasks/{task_id}/submit", response_model=SubmissionOut)
async def submit_task(task_id: UUID, body: SubmissionIn, pool: Pool, cfg: Cfg, llm: Annotated[object, Depends(get_llm)]):
    """Grade a hands-on submission. Three SHORT transactions around one slow LLM call:

    1. record the submission and use an attempt (row-locked; enforces the attempt cap);
    2. the LLM call (10-30 s) with NO database connection held, in a worker thread so the event loop keeps serving;
    3. store the result and, ONLY IF the verified grade passed, complete the task and move the skill matrix.
    If grading itself fails (LLM down, daily budget reached), the attempt is refunded.
    """
    try:
        async with pool.connection() as conn:
            begun = await queries.begin_submission(conn, task_id, body.submission, cfg.max_submission_attempts, cfg.max_submission_chars)
    except queries.SubmissionRejected as exc:
        raise HTTPException(exc.status, exc.detail) from None
    try:
        evaluation = await run_in_threadpool(evaluate, llm, cfg, task=begun["task"], rubric=begun["rubric"], submission=body.submission)
    except LLMBudgetExceeded as exc:
        await _refund(pool, task_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI budget reached: {exc}") from None
    except (LLMError, openai.OpenAIError) as exc:
        await _refund(pool, task_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"the grader failed ({exc.__class__.__name__}); this attempt was not counted. Try again") from None
    async with pool.connection() as conn:
        return await queries.finish_submission(conn, task_id, evaluation.model_dump(), cfg.max_submission_attempts)


async def _refund(pool, task_id: UUID) -> None:
    async with pool.connection() as conn:
        await queries.refund_attempt(conn, task_id)


@router.get("/skills", response_model=list[SkillOut])
async def skills(conn: Conn):
    return await queries.fetch_skills(conn)


@router.get("/feed", response_model=FeedPage)
async def feed(
    conn: Conn, cfg: Cfg,
    category: Annotated[Category | None, Query(description="research | industry_news | engineering_blog | community | newsletter | researcher")] = None,
    skill: Annotated[str | None, Query(max_length=40, pattern=r"^[a-z0-9-]+$", description="only articles whose best-matching skill is this slug")] = None,
    sort: Literal["recent", "relevance"] = "recent",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    days: Annotated[int, Query(ge=1, le=60)] = 14,
    min_strength: Annotated[Literal["moderate", "strong"] | None, Query(description="hide articles weaker than this skill-match bucket")] = None,
):
    try:
        return await queries.fetch_feed(conn, cfg, category=category, skill=skill, sort=sort, limit=limit, cursor=cursor, days=days, min_strength=min_strength)
    except queries.BadCursor as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/briefing/generate", response_model=QueueOut, status_code=status.HTTP_202_ACCEPTED)
async def generate_briefing(conn: Conn, cfg: Cfg, enqueue: Annotated[Callable[[str], None], Depends(get_enqueue)]):
    """For the empty state: ask a Celery worker to generate today's briefing now. Returns immediately (202);
    the UI polls /api/today. LLM generation takes tens of seconds - never do that inside a request."""
    if await queries.briefing_exists(conn, _today(cfg)):
        return QueueOut(queued=False, reason="already generated")
    try:
        # `enqueue` is blocking network I/O (a Redis connection). Calling it directly inside `async def` would
        # freeze the event loop - and every other request - while it waits. Offload blocking calls to a thread.
        await run_in_threadpool(enqueue, "app.tasks.generate_daily_briefing")
    except Exception as exc:  # broker down
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "task queue unavailable") from exc
    return QueueOut(queued=True)


# ----------------------------------------------------------------------------------------------- Phase 11: Bitcoin
@router.get("/bitcoin/latest", response_model=BitcoinLatestOut)
async def bitcoin_latest(conn: Conn):
    """Latest BTC price, the latest AI forecast (4 horizons with confidence + reasoning) and recent news.
    Before the first run, `price` and `prediction` are null. That is a normal 200, not an error."""
    return await queries.fetch_bitcoin_latest(conn)


@router.post("/bitcoin/generate", response_model=QueueOut, status_code=status.HTTP_202_ACCEPTED)
async def bitcoin_generate(conn: Conn, cfg: Cfg, enqueue: Annotated[Callable[..., None], Depends(get_enqueue)]):
    """Manual trigger (the dashboard's Refresh button). Like /briefing/generate it only ENQUEUES: fetching feeds plus an
    LLM call takes 15-60 s, far too long to hold an HTTP request. The UI polls /bitcoin/latest until a newer forecast
    appears. A cooldown stops repeated clicks from paying for identical forecasts."""
    if await queries.bitcoin_generated_recently(conn, cfg.bitcoin_regen_cooldown_minutes):
        return QueueOut(queued=False, reason=f"a forecast was generated in the last {cfg.bitcoin_regen_cooldown_minutes} minutes")
    try:
        await run_in_threadpool(enqueue, "app.tasks.generate_bitcoin_daily_insight", trigger="manual")
    except Exception as exc:  # broker down
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "task queue unavailable") from exc
    return QueueOut(queued=True)
