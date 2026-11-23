"""Mastery-loop endpoints: spaced review, the weekly retrospective, the math drill, and the dashboard summary.

The loop, in one picture:

    chapter / week / primary source ──► review cards ──► Review (answer from memory) ──► SM-2 schedule ──► due again later
                                                                                        │
    overdue cards or a missing retrospective ──────────────────────► 423 on new tasks ◄─┘   (the gate)
    retrospective (evidence per milestone + decision) ──► next week's planner prompt     (the feedback)
    math drill (generated + graded by code) ──► math-foundations skill, missed topics resurface
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import openai
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import Cfg, Conn, Pool, get_llm, require_token
from app.api.schemas import (
    CatchupItem,
    GradeIn,
    GradeOut,
    MasteryOut,
    MathAnswerIn,
    MathAnswerOut,
    MathDrillOut,
    RecallIn,
    RecallOut,
    RetroIn,
    RetroOut,
    ReviewCardOut,
    ReviewReferenceOut,
    SuspendIn,
)
from app.curriculum import prompts
from app.curriculum.errors import LLMBudgetExceeded, LLMError
from app.curriculum.schemas import GroundingContext, RecallCheck
from app.mastery import queries as mq
from app.mastery import srs

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _today(cfg) -> date:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _raise(exc: mq.Rejected):
    raise HTTPException(exc.status, exc.detail) from None


@router.get("/mastery", response_model=MasteryOut)
async def mastery(conn: Conn, cfg: Cfg):
    """One round trip for the dashboard: reviews due, the gate (and why), last week's retro, today's math."""
    return await mq.status(conn, cfg, _today(cfg))


@router.get("/catchup", response_model=list[CatchupItem])
async def catchup(conn: Conn, cfg: Cfg):
    """Lessons you did not finish in the last CATCHUP_DAYS days. They stay doable (late) until `expires_on`."""
    return await mq.catchup(conn, _today(cfg), cfg.catchup_days)


# ------------------------------------------------------------------------------------------------ reviews
@router.get("/reviews", response_model=list[ReviewCardOut])
async def reviews_due(conn: Conn, cfg: Cfg, limit: Annotated[int | None, Query(ge=1, le=100)] = None):
    """Cards due today or earlier, oldest first. The reference text is NOT included: answer first, then compare."""
    return await mq.due_cards(conn, _today(cfg), limit or cfg.review_batch_limit)


@router.get("/reviews/{card_id}/reference", response_model=ReviewReferenceOut)
async def review_reference(card_id: UUID, conn: Conn):
    row = await mq.card(conn, card_id, with_context=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    return {"id": row["id"], "context": row["context"]}


@router.post("/reviews/{card_id}/check", response_model=RecallOut)
async def review_check(card_id: UUID, body: RecallIn, pool: Pool, cfg: Cfg, llm: Annotated[object, Depends(get_llm)]):
    """Optional AI check of an answer written from memory, judged against the card's reference. Schedules nothing:
    the learner then grades (and may override the suggestion). The LLM call holds no DB connection."""
    async with pool.connection() as conn:
        row = await mq.card(conn, card_id, with_context=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    try:
        out: RecallCheck = await run_in_threadpool(
            llm.generate, system=prompts.RECALL_SYSTEM, user=prompts.build_recall_prompt(row["prompt"], row["context"], body.answer),
            output_model=RecallCheck, grounding=GroundingContext(frozenset(), frozenset()), temperature=cfg.temp_recall)
    except LLMBudgetExceeded as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI budget reached: {exc}. Grade yourself against the reference instead.") from None
    except (LLMError, openai.OpenAIError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"the checker failed ({exc.__class__.__name__}); grade yourself against the reference") from None
    return {**out.model_dump(), "suggested_grade": srs.grade_from_verdict(out.verdict)}


@router.post("/reviews/{card_id}/grade", response_model=GradeOut)
async def review_grade(card_id: UUID, body: GradeIn, conn: Conn, cfg: Cfg):
    """Record one review and schedule the next (SM-2). Grading the same card twice in a day simply reschedules it."""
    out = await mq.grade_card(conn, card_id, body.grade, body.answer, body.feedback, _today(cfg))
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    return out


@router.post("/reviews/{card_id}/suspend", response_model=ReviewCardOut)
async def review_suspend(card_id: UUID, body: SuspendIn, conn: Conn):
    """For a badly-worded or duplicate card. Suspended cards are never due and never gate anything."""
    row = await mq.suspend_card(conn, card_id, body.suspended)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card not found")
    return row


# ------------------------------------------------------------------------------------------------ retrospective
def _monday(d: date) -> date:
    if d.weekday() != 0:
        raise HTTPException(422, "week_start must be a Monday (YYYY-MM-DD)")
    return d


@router.get("/retro/{week_start}", response_model=RetroOut)
async def retro_get(week_start: date, conn: Conn, cfg: Cfg):
    return await mq.retro_view(conn, cfg, _monday(week_start), _today(cfg))


@router.put("/retro/{week_start}", response_model=RetroOut)
async def retro_put(week_start: date, body: RetroIn, conn: Conn, cfg: Cfg):
    """Submit the weekly retrospective (once). Requires: the week is over (Sunday or later), every teach-back question
    answered in Review, evidence for every milestone, and on a quarter's last week the public artifact's link."""
    try:
        return await mq.submit_retro(conn, cfg, _monday(week_start), _today(cfg), body.model_dump())
    except mq.Rejected as exc:
        _raise(exc)


# ------------------------------------------------------------------------------------------------ math drill
@router.get("/math/today", response_model=MathDrillOut)
async def math_today(conn: Conn, cfg: Cfg):
    return await mq.math_drill(conn, cfg, _today(cfg))


@router.post("/math/{day}/{idx}/answer", response_model=MathAnswerOut)
async def math_answer(day: date, idx: int, body: MathAnswerIn, conn: Conn, cfg: Cfg):
    """Graded by code against the exact answer. Only today's drill accepts answers; only the first attempt counts."""
    if day != _today(cfg):
        raise HTTPException(status.HTTP_409_CONFLICT, "only today's drill accepts answers")
    try:
        return await mq.answer_math(conn, cfg, day, idx, body.answer)
    except mq.Rejected as exc:
        _raise(exc)
