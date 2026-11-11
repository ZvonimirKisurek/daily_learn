from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import openai
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api import queries
from app.api.deps import Cfg, Conn, Pool, QueueUnavailable, get_enqueue, get_llm, require_token
from app.api.schemas import (AnswersIn, BitcoinPulseOut, BetAdviceOut, BetCheckIn, BetCheckOut, BetIn, BetOverviewOut, BitcoinLatestOut, CompletionOut, ExamOut, ExamResultOut, ExamStartIn, NotesIn, NotesOut, FeedPage,
                             ProgressOut, QueueOut, QuizOut, SkillOut, SubmissionIn, SubmissionOut, TaskDetailOut, TodayOut)
from app.bitcoin import advisor, live
from app.curriculum import levels
from app.curriculum.errors import LLMBudgetExceeded, LLMError
from app.curriculum.evaluation import evaluate
from app.curriculum.generators import generate_level_exam
from app.ingestion.models import Category

# Auth applies to the whole router; /health lives outside it.
router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
# Which learning track a request is about: the AI/ML path (default) or the management path.
Track = Annotated[Literal["ai", "management"], Query(description="learning track")]


def _today(cfg) -> "datetime.date":
    return datetime.now(ZoneInfo(cfg.timezone)).date()


@router.get("/today", response_model=TodayOut)
async def today(conn: Conn, cfg: Cfg, track: Track = "ai"):
    """Everything the Command Center needs in one round trip. `state="pending"` (briefing null) is a normal,
    expected response before 06:30 or if generation failed - it is NOT an error, so it is a 200."""
    return await queries.fetch_today(conn, _today(cfg), track)


@router.get("/briefings/history", response_model=list[date])
async def briefing_history(conn: Conn, track: Track = "ai"):
    """Every date (YYYY-MM-DD, oldest first) that has a generated briefing. The calendar uses it to know which days are clickable.
    Declared BEFORE /briefings/{day}: FastAPI matches routes in registration order, and 'history' must not be parsed as a date."""
    return await queries.list_briefing_days(conn, track)


@router.get("/briefings/done", response_model=list[date])
async def briefing_done_days(conn: Conn, track: Track = "ai"):
    """Days whose lesson is fully finished (every task done). The calendar paints them green, the others yellow.
    Declared before /briefings/{day} for the same reason as /history."""
    return await queries.list_completed_days(conn, track)


@router.get("/briefings/{day}", response_model=TodayOut)
async def briefing_for_day(day: date, conn: Conn, cfg: Cfg, track: Track = "ai"):
    """One day's briefing, same shape as /api/today. Today behaves exactly like /api/today (a missing briefing is a 200 'pending').
    Any other day must have a stored briefing: past-without-data and future dates are a 404, so the UI never offers an empty page."""
    if day != _today(cfg) and not await queries.briefing_exists(conn, day, track):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no briefing for that date")
    return await queries.fetch_today(conn, day, track)


@router.post("/tasks/{task_id}/complete", response_model=CompletionOut)
async def complete_task(task_id: UUID, conn: Conn):
    """Idempotent: completing twice returns `already_completed: true` and awards nothing the second time."""
    try:
        result = await queries.complete_task(conn, task_id)
    except queries.SubmissionRequired:
        raise HTTPException(status.HTTP_409_CONFLICT, "hands-on tasks are completed by submitting your work for evaluation") from None
    except queries.QuizRequired:
        raise HTTPException(status.HTTP_409_CONFLICT, "this reading is completed by passing its quiz") from None
    except queries.Locked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail) from None
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return result


@router.get("/tasks/{task_id}", response_model=TaskDetailOut)
async def task_detail(task_id: UUID, conn: Conn, cfg: Cfg):
    """Everything a task page needs: the chapter, or the project + rubric + your last submission and feedback."""
    detail = await queries.get_task_detail(conn, task_id, cfg.max_submission_attempts)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    # A past day's legacy lab is read-only; learning-path lessons can be caught up any time (they count toward the exam).
    detail["archived"] = detail["day"] < _today(cfg) and detail["lesson"] is None
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


@router.post("/tasks/{task_id}/quiz", response_model=QuizOut)
async def submit_quiz(task_id: UUID, body: AnswersIn, conn: Conn):
    """Grade the reading quiz (deterministic: answer keys never leave the server). Passing completes the reading and
    unlocks the hands-on exercise. Unlimited attempts, but a failed attempt does not reveal the right options."""
    try:
        return await queries.submit_quiz(conn, task_id, body.answers)
    except queries.SubmissionRejected as exc:
        raise HTTPException(exc.status, exc.detail) from None


# ----------------------------------------------------------------------------------------------- learning path
@router.get("/progress", response_model=ProgressOut)
async def progress(conn: Conn, cfg: Cfg, track: Track = "ai"):
    """Level, XP, streak, knowledge score, exam status and the whole path (completed / current / locked) of one track."""
    return await queries.fetch_progress(conn, _today(cfg), cfg.timezone, levels.ladder(track), cfg.exam_cooldown_hours, track)


@router.post("/exams/start", response_model=ExamOut)
async def start_exam(body: ExamStartIn, pool: Pool, cfg: Cfg, llm: Annotated[object, Depends(get_llm)]):
    """Placement: a shuffled copy of the static question bank. Level: unlocks after the level's lessons are finished;
    an unanswered exam is reused, otherwise the LLM writes a fresh one (10-40 s, outside any transaction)."""
    track = body.track
    lad, today = levels.ladder(track), _today(cfg)
    bank = None
    if body.kind == "placement":
        import random
        rng = random.Random()
        bank = [levels.shuffle_question(q.model_dump(), rng) for q in levels.placement_bank(track).questions]
    try:
        async with pool.connection() as conn:
            prep = await queries.prepare_exam(conn, body.kind, today, cfg.timezone, lad, cfg.exam_cooldown_hours, bank, track)
    except queries.SubmissionRejected as exc:
        raise HTTPException(exc.status, exc.detail) from None
    if "exam" in prep:
        return prep["exam"]
    try:
        questions = await run_in_threadpool(generate_level_exam, prep["generate"], prep["titles"], llm=llm, settings=cfg, lad=lad, track=track)
    except LLMBudgetExceeded as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI budget reached: {exc}") from None
    except (LLMError, openai.OpenAIError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not write the exam ({exc.__class__.__name__}); try again") from None
    async with pool.connection() as conn:
        return await queries.store_level_exam(conn, prep["generate"], questions, lad, track)


@router.post("/exams/{exam_id}/submit", response_model=ExamResultOut)
async def submit_exam(exam_id: UUID, body: AnswersIn, conn: Conn):
    """Grade an exam by its stored answer key. Placement can only move you UP; a passed level exam unlocks the next level."""
    try:
        return await queries.submit_exam(conn, exam_id, body.answers)
    except queries.SubmissionRejected as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.get("/skills", response_model=list[SkillOut])
async def skills(conn: Conn):
    return await queries.fetch_skills(conn)


@router.get("/feed", response_model=FeedPage)
async def feed(
    conn: Conn, cfg: Cfg,
    category: Annotated[Category | None, Query(description="research | industry_news | engineering_blog | community | newsletter | researcher")] = None,
    skill: Annotated[str | None, Query(max_length=40, pattern=r"^[a-z0-9-]+$", description="only articles whose best-matching skill is this slug")] = None,
    sort: Literal["recent", "relevance", "for_you"] = "recent",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    days: Annotated[int, Query(ge=1, le=60)] = 14,
    min_strength: Annotated[Literal["moderate", "strong"] | None, Query(description="hide articles weaker than this skill-match bucket")] = None,
    track: Track = "ai",
):
    for_level, vec = (None, None)
    if sort == "for_you":
        for_level, vec = await queries.level_query_vector(conn, levels.ladder(track), track)
    try:
        page = await queries.fetch_feed(conn, cfg, category=category, skill=skill, sort=sort, limit=limit, cursor=cursor, days=days,
                                        min_strength=min_strength, level_vec=vec, track=track)
        return {**page, "for_level": for_level if vec is not None else None}
    except queries.BadCursor as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/briefing/generate", response_model=QueueOut, status_code=status.HTTP_202_ACCEPTED)
async def generate_briefing(conn: Conn, cfg: Cfg, enqueue: Annotated[Callable[..., None], Depends(get_enqueue)], track: Track = "ai"):
    """For the empty state: ask a Celery worker to generate today's briefing now. Returns immediately (202);
    the UI polls /api/today. LLM generation takes tens of seconds - never do that inside a request."""
    if await queries.briefing_exists(conn, _today(cfg), track):
        return QueueOut(queued=False, reason="already generated")
    try:
        # `enqueue` is blocking network I/O (a Redis connection). Calling it directly inside `async def` would
        # freeze the event loop - and every other request - while it waits. Offload blocking calls to a thread.
        # the AI track keeps the original one-argument call; other tracks say which lesson they want
        await run_in_threadpool(lambda: enqueue("app.tasks.generate_daily_briefing", **({} if track == "ai" else {"track": track})))
    except Exception as exc:  # broker down and inline fallback disabled
        msg = str(exc) if isinstance(exc, QueueUnavailable) else "task queue unavailable"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, msg) from exc
    return QueueOut(queued=True)


# ----------------------------------------------------------------------------------------------- Phase 11: Bitcoin
_BTC_MISSING = ("Bitcoin tables are missing in this database. Apply supabase/migrations/20261108000008_bitcoin.sql and "
                "20261109000009_bitcoin_runs.sql, then reload.")


@router.get("/bitcoin/latest", response_model=BitcoinLatestOut)
async def bitcoin_latest(conn: Conn):
    """Latest BTC price, the latest AI forecast (4 horizons with confidence + reasoning), recent news, and the status of
    the most recent run. Before the first run everything is null/empty: a normal 200, not an error."""
    try:
        return await queries.fetch_bitcoin_latest(conn)
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _BTC_MISSING) from None


@router.post("/bitcoin/generate", response_model=QueueOut, status_code=status.HTTP_202_ACCEPTED)
async def bitcoin_generate(pool: Pool, cfg: Cfg, enqueue: Annotated[Callable[..., None], Depends(get_enqueue)]):
    """Manual trigger (the dashboard's Refresh button). Only ENQUEUES: feeds + an LLM call take 15-60 s, far too long to
    hold an HTTP request. A `bitcoin_runs` row (status `queued`) is created first and its id travels with the job, so the
    UI can show queued -> running -> succeeded/failed (with the reason) instead of polling blindly.

    Explicit short transactions (not the per-request `Conn`): the run row must be COMMITTED before the job starts, or a
    fast inline job could look for a row that isn't visible yet."""
    try:
        async with pool.connection() as conn:
            if await queries.bitcoin_generated_recently(conn, cfg.bitcoin_regen_cooldown_minutes):
                return QueueOut(queued=False, reason=f"A forecast was generated in the last {cfg.bitcoin_regen_cooldown_minutes} minutes.")
            if active := await queries.active_bitcoin_run(conn):
                return QueueOut(queued=True, reason="An analysis is already running.", run_id=active["id"])
            run_id = await queries.create_bitcoin_run(conn, "manual")
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _BTC_MISSING) from None
    try:
        await run_in_threadpool(enqueue, "app.tasks.generate_bitcoin_daily_insight", trigger="manual", run_id=str(run_id))
    except Exception as exc:
        msg = str(exc) if isinstance(exc, QueueUnavailable) else "Analysis engine offline: could not start the job. Please try again later."
        async with pool.connection() as conn:
            await queries.fail_bitcoin_run(conn, run_id, msg)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, msg) from exc
    return QueueOut(queued=True, run_id=run_id)


@router.post("/bitcoin/pulse", response_model=BitcoinPulseOut)
async def bitcoin_pulse(cfg: Cfg, enqueue: Annotated[Callable[..., None], Depends(get_enqueue)]):
    """Called by the Bitcoin page on open and about once a minute while its tab is visible: refresh the price and the
    news, and start a forecast when fresh Bitcoin headlines arrived since the last one. No visitor, no work."""
    try:
        return await run_in_threadpool(live.pulse, cfg, enqueue)
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _BTC_MISSING) from None
    except psycopg.errors.CheckViolation:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Apply supabase/migrations/20261111000012_bitcoin_live.sql, then reload.") from None


_BET_MISSING = "The bet advisor's table is missing. Apply supabase/migrations/20261110000011_bitcoin_bets.sql, then reload."


async def _advisor(fn, *args, **kw):
    """The advisor is synchronous (one outbound price request + small queries): run it in a worker thread."""
    try:
        return await run_in_threadpool(fn, *args, **kw)
    except advisor.AdvisorError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _BET_MISSING) from None


@router.get("/bitcoin/bet", response_model=BetOverviewOut)
async def bitcoin_bet_overview(cfg: Cfg):
    """Bankroll state (active / vault / peak), the latest recommendation, recent history and the rule parameters."""
    return await _advisor(advisor.overview, cfg)


@router.post("/bitcoin/bet", response_model=BetAdviceOut)
async def bitcoin_bet_advise(body: BetIn, cfg: Cfg):
    """The daily check-in: report your balance and the market's price for 'BTC above $strike'; get direction and size.
    Pure arithmetic on the latest forecast (betting.py): edge vs price, quarter-Kelly, a hard cap, and the vault sweep."""
    return await _advisor(advisor.advise, cfg, **body.model_dump())


@router.post("/bitcoin/bet/check", response_model=BetCheckOut)
async def bitcoin_bet_check(body: BetCheckIn, cfg: Cfg):
    """Before settlement: sell the open position at the current price, or keep holding? Nothing is stored."""
    return await _advisor(advisor.check, cfg, sell_price=body.sell_price)
