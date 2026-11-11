"""Async SQL for the API. Every function takes the request's connection; none opens its own transaction."""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.config import Settings
from app.curriculum import levels as lv
from app.curriculum.skills import apply_delta, compute_delta


class BadCursor(ValueError):
    pass


class SubmissionRequired(Exception):
    """A hands-on task cannot be ticked off: it is completed only by a PASSING graded submission."""


class QuizRequired(Exception):
    """A reading with a quiz is completed only by PASSING the quiz, never by a tick."""


class Locked(Exception):
    """Duolingo-style order inside a lesson: reading quiz -> hands-on -> essay."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SubmissionRejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


TASK_COLS = ("id, kind, skill_slug, difficulty, title, completed_at, points_awarded, attempt_count, evaluation_score, "
             "evaluation_passed, (reading_content is not null) as has_content, (quiz is not null) as has_quiz, quiz_attempts, "
             "quiz_score, xp_awarded")


# ----------------------------------------------------------------------------------------------- skills
async def fetch_skills(conn: AsyncConnection) -> list[dict]:
    skills = await (await conn.execute("select slug, name, level, target, track from skills order by track, name")).fetchall()
    hist = await (await conn.execute(
        """select s.slug, h.level, h.recorded_at, h.delta from skill_history h join skills s on s.id = h.skill_id
           where h.recorded_at > now() - interval '30 days' order by h.recorded_at""")).fetchall()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    out = []
    for s in skills:
        points = [h for h in hist if h["slug"] == s["slug"]]
        out.append({
            "slug": s["slug"], "name": s["name"], "track": s["track"], "level": round(s["level"], 2), "target": s["target"],
            "percent_to_target": round(min(100.0, s["level"] / max(s["target"], 1) * 100), 1),
            "delta_7d": round(sum((h["delta"] or 0) for h in points if h["recorded_at"] >= week_ago), 3),
            "history": [{"at": h["recorded_at"], "level": h["level"]} for h in points],
        })
    return out


# ----------------------------------------------------------------------------------------------- today
async def _tasks(conn: AsyncConnection, briefing_id) -> list[dict]:
    rows = await (await conn.execute(
        f"""select {TASK_COLS} from daily_tasks
           where briefing_id=%s order by case kind when 'reading' then 1 when 'hands_on' then 2 else 3 end""",
        (briefing_id,))).fetchall()
    return [{**r, "completed": r["completed_at"] is not None} for r in rows]


async def fetch_today(conn: AsyncConnection, day: date, track: str = "ai") -> dict:
    b = await (await conn.execute("select id, day, completed_at, payload, theme_id, level, track from daily_briefings where day=%s and track=%s",
                                  (day, track))).fetchone()
    week_start = day - timedelta(days=day.weekday())
    t = await (await conn.execute("select id, week_start, payload from weekly_themes where week_start=%s", (week_start,))).fetchone()
    return {
        "date": day, "track": track,
        "state": "ready" if b else "pending",
        "briefing": None if b is None else {"id": b["id"], "day": b["day"], "completed_at": b["completed_at"], "level": b["level"], "track": b["track"],
                                            "payload": b["payload"], "tasks": await _tasks(conn, b["id"])},
        "weekly_theme": None if t is None or track != "ai" else {"id": t["id"], "week_start": t["week_start"], "theme": t["payload"]},
        "skills": await fetch_skills(conn),
    }


async def list_briefing_days(conn: AsyncConnection, track: str = "ai") -> list[date]:
    """Every day that has a stored lesson on this track, oldest first."""
    rows = await (await conn.execute("select day from daily_briefings where track=%s order by day", (track,))).fetchall()
    return [r["day"] for r in rows]


async def list_completed_days(conn: AsyncConnection, track: str = "ai") -> list[date]:
    rows = await (await conn.execute("select day from daily_briefings where completed_at is not null and track=%s order by day", (track,))).fetchall()
    return [r["day"] for r in rows]


async def briefing_exists(conn: AsyncConnection, day: date, track: str = "ai") -> bool:
    return (await (await conn.execute("select 1 from daily_briefings where day=%s and track=%s", (day, track))).fetchone()) is not None


# ----------------------------------------------------------------------------------------------- completion
async def complete_task(conn: AsyncConnection, task_id: UUID, *, via_evaluation: bool = False, via_quiz: bool = False,
                        xp: int | None = None) -> dict | None:
    """Mark a task done and move its skill - atomically, idempotently, and safe under concurrency.

    * The conditional UPDATE (`completed_at is null`) is the idempotency guard. Two simultaneous requests
      both try it; Postgres lets one proceed and makes the other WAIT on the row lock, then re-evaluates the
      condition, finds it false, and updates zero rows. Exactly one request wins and awards points.
    * `select ... for update` on the skill row serialises read-modify-write of the level (two different tasks
      on the same skill completed at the same instant must not overwrite each other's increment).
    * All of this runs in the request's transaction (Conn dependency): it commits together or not at all.
    """
    kind_row = await (await conn.execute(
        """select t.kind, t.quiz is not null as has_quiz, t.completed_at is not null as done, b.level, b.id as briefing_id
           from daily_tasks t join daily_briefings b on b.id = t.briefing_id where t.id=%s""", (task_id,))).fetchone()
    if kind_row is not None and kind_row["kind"] == "hands_on" and not via_evaluation:
        raise SubmissionRequired  # hands-on work is completed by a passing graded submission, never by a tick
    if kind_row is not None and kind_row["kind"] == "reading" and kind_row["has_quiz"] and not via_quiz:
        raise QuizRequired
    if kind_row is not None and kind_row["kind"] == "insight" and kind_row["level"] is not None and not kind_row["done"]:
        blocking = await (await conn.execute(
            "select count(*) as n from daily_tasks where briefing_id=%s and kind <> 'insight' and completed_at is null",
            (kind_row["briefing_id"],))).fetchone()
        if blocking["n"]:
            raise Locked("finish the reading quiz and the hands-on exercise first")
    task = await (await conn.execute(
        "update daily_tasks set completed_at=now() where id=%s and completed_at is null "
        "returning id, briefing_id, kind, skill_slug, difficulty", (task_id,))).fetchone()
    if task is None:
        row = await (await conn.execute("select briefing_id, points_awarded, skill_slug from daily_tasks where id=%s", (task_id,))).fetchone()
        if row is None:
            return None
        skill = await (await conn.execute("select level from skills where slug=%s", (row["skill_slug"],))).fetchone()
        done = await (await conn.execute("select completed_at is not null as d from daily_briefings where id=%s", (row["briefing_id"],))).fetchone()
        return await _completion(conn, task_id, row["skill_slug"], float(row["points_awarded"] or 0), float(skill["level"]), done["d"], True)

    skill = await (await conn.execute("select level from skills where slug=%s for update", (task["skill_slug"],))).fetchone()
    delta = compute_delta(task["kind"], task["difficulty"], float(skill["level"]))
    new_level = apply_delta(float(skill["level"]), delta)
    await conn.execute("update skills set level=%s, updated_at=now() where slug=%s", (new_level, task["skill_slug"]))
    await conn.execute("update daily_tasks set points_awarded=%s where id=%s", (delta, task_id))
    await conn.execute("insert into skill_history (skill_id, level, delta, task_id) select id, %s, %s, %s from skills where slug=%s",
                       (new_level, delta, task_id, task["skill_slug"]))
    gained = xp if xp is not None else (lv.XP_INSIGHT if task["kind"] == "insight" else lv.XP_QUIZ // 2)
    await award_xp(conn, gained, f"{task['kind']} completed", task_id=task_id)
    await conn.execute("update daily_tasks set xp_awarded=%s where id=%s", (gained, task_id))
    remaining = (await (await conn.execute(
        "select count(*) as n from daily_tasks where briefing_id=%s and completed_at is null", (task["briefing_id"],))).fetchone())["n"]
    if remaining == 0:
        b = await (await conn.execute("update daily_briefings set completed_at=now() where id=%s returning level", (task["briefing_id"],))).fetchone()
        if b["level"] is not None:  # a finished lesson on the learning path
            await award_xp(conn, lv.XP_LESSON_BONUS, "lesson finished", task_id=task_id)
            gained += lv.XP_LESSON_BONUS
    return await _completion(conn, task_id, task["skill_slug"], delta, new_level, remaining == 0, False, gained)


async def award_xp(conn: AsyncConnection, amount: int, reason: str, *, task_id=None, exam_id=None) -> None:
    if amount:
        await conn.execute("insert into xp_events (amount, reason, task_id, exam_id) values (%s,%s,%s,%s)", (amount, reason, task_id, exam_id))


async def _completion(conn, task_id, slug, delta, level, briefing_done, already, xp: int = 0) -> dict:
    task = await (await conn.execute(f"select {TASK_COLS} from daily_tasks where id=%s", (task_id,))).fetchone()
    return {"task": {**task, "completed": task["completed_at"] is not None},
            "skill": {"slug": slug, "level": round(level, 3), "delta": delta},
            "briefing_completed": briefing_done, "already_completed": already, "xp": xp}


# ----------------------------------------------------------------------------------------------- task detail + grading
async def get_task_detail(conn: AsyncConnection, task_id: UUID, max_attempts: int) -> dict | None:
    row = await (await conn.execute(
        """select t.id, t.kind, t.skill_slug, t.difficulty, t.title, t.completed_at, t.points_awarded, t.attempt_count,
                  t.evaluation_score, t.evaluation_passed, (t.reading_content is not null) as has_content,
                  t.reading_content, t.notes, t.user_submission, t.evaluation_result, t.quiz, t.quiz_attempts, t.quiz_score,
                  t.xp_awarded, b.day, b.payload, b.level, b.track
           from daily_tasks t join daily_briefings b on b.id = t.briefing_id where t.id = %s""", (task_id,))).fetchone()
    if row is None:
        return None
    payload, kind = row["payload"], row["kind"]
    task = {k: row[k] for k in ("id", "kind", "skill_slug", "difficulty", "title", "completed_at", "points_awarded", "attempt_count",
                                "evaluation_score", "evaluation_passed", "has_content", "quiz_attempts", "quiz_score", "xp_awarded")}
    task["has_quiz"] = row["quiz"] is not None
    task["completed"] = row["completed_at"] is not None
    locked = None
    if row["level"] is not None and kind != "reading" and not task["completed"]:
        open_ = await (await conn.execute(
            """select array_agg(kind) as k from daily_tasks where briefing_id = (select briefing_id from daily_tasks where id=%s)
               and completed_at is null and kind <> %s""", (task_id, kind))).fetchone()
        need = [k for k in (open_["k"] or []) if (k == "reading") or (kind == "insight" and k == "hands_on")]
        if need:
            locked = "Pass the reading quiz first." if "reading" in need else "Pass the hands-on exercise first."
    return {
        "task": task, "day": row["day"], "attempts_max": max_attempts, "attempts_used": row["attempt_count"],
        "reading": {**payload["morning_reading"], "content": row["reading_content"]} if kind == "reading" else None,
        "hands_on": payload["hands_on_task"] if kind == "hands_on" else None,
        "insight": payload["market_insight"] if kind == "insight" else None,
        "notes": row["notes"], "submission": row["user_submission"], "evaluation": row["evaluation_result"],
        "quiz": lv.public_questions(row["quiz"]) if row["quiz"] and kind == "reading" else None,
        "lesson": payload.get("lesson"), "locked": locked, "track": row["track"],
    }


async def get_notes(conn: AsyncConnection, task_id: UUID) -> dict | None:
    return await (await conn.execute("select notes, notes_updated_at as updated_at from daily_tasks where id=%s", (task_id,))).fetchone()


async def save_notes(conn: AsyncConnection, task_id: UUID, notes: str) -> dict | None:
    """One primary-key UPDATE ... RETURNING: a single round trip, no read-before-write, last write wins (it is a single-user notebook)."""
    return await (await conn.execute(
        "update daily_tasks set notes=%s, notes_updated_at=now() where id=%s returning notes, notes_updated_at as updated_at",
        (notes, task_id))).fetchone()


async def begin_submission(conn: AsyncConnection, task_id: UUID, submission: str, max_attempts: int, max_chars: int) -> dict:
    """Validate that this task can be graded, then record the submission and use up an attempt, atomically.
    The row lock (`for update`) serialises concurrent submissions; the attempt counter is the per-task cost guard."""
    row = await (await conn.execute(
        """select t.kind, t.completed_at, t.evaluation_rubric, t.attempt_count, b.payload
           from daily_tasks t join daily_briefings b on b.id = t.briefing_id where t.id = %s for update of t""", (task_id,))).fetchone()
    if row is None:
        raise SubmissionRejected(404, "task not found")
    if row["kind"] != "hands_on" or not row["evaluation_rubric"]:
        raise SubmissionRejected(409, "only hands-on tasks with a rubric can be submitted")
    if row["completed_at"] is not None:
        raise SubmissionRejected(409, "you already passed this task")
    reading_open = await (await conn.execute(
        """select 1 from daily_tasks r join daily_briefings b on b.id = r.briefing_id
           where r.briefing_id = (select briefing_id from daily_tasks where id=%s) and b.level is not null
             and r.kind = 'reading' and r.completed_at is null""", (task_id,))).fetchone()
    if reading_open:
        raise SubmissionRejected(409, "pass the reading quiz first: the hands-on exercise unlocks after it")
    if len(submission) > max_chars:
        raise SubmissionRejected(413, f"submission is {len(submission):,} characters; the limit is {max_chars:,}")
    if row["attempt_count"] >= max_attempts:
        raise SubmissionRejected(429, f"attempt limit reached ({max_attempts}); study the feedback and try again tomorrow")
    await conn.execute("update daily_tasks set attempt_count = attempt_count + 1, user_submission = %s, submitted_at = now() where id = %s",
                       (submission, task_id))
    return {"task": row["payload"]["hands_on_task"], "rubric": row["evaluation_rubric"], "attempts_used": row["attempt_count"] + 1}


async def refund_attempt(conn: AsyncConnection, task_id: UUID) -> None:
    """An infrastructure failure (LLM down, budget reached) must not cost the learner an attempt."""
    await conn.execute("update daily_tasks set attempt_count = greatest(attempt_count - 1, 0) where id = %s", (task_id,))


async def finish_submission(conn: AsyncConnection, task_id: UUID, evaluation: dict, max_attempts: int) -> dict:
    """Store the graded result; if (and only if) it PASSED, complete the task, which moves the skill matrix.
    A concurrent request that already passed is never overwritten by a later failing evaluation."""
    await conn.execute(
        """update daily_tasks set evaluation_result = %(r)s, evaluation_score = %(s)s, evaluation_passed = %(p)s,
               ai_evaluation_feedback = %(f)s, evaluated_at = now()
           where id = %(id)s and (completed_at is null or %(p)s)""",
        {"r": Jsonb(evaluation), "s": evaluation["score"], "p": evaluation["passed"], "f": evaluation["feedback_markdown"], "id": task_id})
    completion = (await complete_task(conn, task_id, via_evaluation=True, xp=lv.hands_on_xp(evaluation["score"]))
                  if evaluation["passed"] else None)
    task = await (await conn.execute(f"select {TASK_COLS} from daily_tasks where id=%s", (task_id,))).fetchone()
    done = await (await conn.execute(
        "select b.completed_at is not null as d from daily_tasks t join daily_briefings b on b.id=t.briefing_id where t.id=%s", (task_id,))).fetchone()
    return {"task": {**task, "completed": task["completed_at"] is not None}, "evaluation": evaluation, "passed": evaluation["passed"],
            "skill": completion["skill"] if completion and not completion["already_completed"] else None,
            "briefing_completed": done["d"], "attempts_used": task["attempt_count"], "attempts_max": max_attempts,
            "xp": completion["xp"] if completion and not completion["already_completed"] else 0}


# ----------------------------------------------------------------------------------------------- feed
def _encode(sort: str, key, row_id) -> str:
    return base64.urlsafe_b64encode(json.dumps([sort, key.isoformat() if isinstance(key, datetime) else key, str(row_id)]).encode()).decode()


def _decode(cursor: str, sort: str):
    try:
        s, key, rid = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if s != sort:
            raise ValueError("cursor belongs to a different sort order")
        return (datetime.fromisoformat(key) if sort == "recent" else float(key)), UUID(rid)
    except Exception as exc:
        raise BadCursor("invalid cursor") from exc


def _strength(sim: float, s: Settings) -> str:
    return "strong" if sim >= s.match_strong else "moderate" if sim >= s.match_moderate else "weak"


async def fetch_feed(
    conn: AsyncConnection, settings: Settings, *, category: str | None, skill: str | None, sort: str,
    limit: int, cursor: str | None, days: int, min_strength: str | None = None, level_vec=None, track: str = "ai",
) -> dict:
    """Keyset-paginated feed of canonical (de-duplicated) articles.

    Dedup in the data flow: ingestion stored near-identical stories with `duplicate_of` pointing at the first
    one seen; here `duplicate_of is null` hides the copies and `also_covered_by` reports which other sources
    carried the story - so the UI can show "also on The Verge, TechCrunch" instead of five rows.

    Relevance in the data flow: the LATERAL subquery finds, per article, the nearest skill vector by cosine
    distance (`<=>`). similarity = 1 - distance. `relevance = similarity * (0.5 + 0.5 * gap)` boosts matches to
    skills where you are furthest from target. Cosine values for article-vs-skill-description text are
    typically 0.15-0.5, NOT percentages: the API therefore also returns a coarse `strength` bucket whose
    thresholds you calibrate with `python -m app.cli feed-stats`.

    Keyset vs OFFSET: `OFFSET 1000` makes Postgres read and discard 1000 rows, and rows inserted meanwhile shift
    the pages (duplicates/skips). Keyset resumes strictly after the last (sort_key, id) seen - stable and fast.
    """
    key = "ts" if sort == "recent" else "relevance"
    # "For you": rank by closeness to what you are learning NOW (your level's query vector), not to your skill gaps.
    for_you = sort == "for_you" and level_vec is not None
    relevance_sql = ("coalesce((1 - (a.embedding <=> %(lvec)s))::double precision, 0)" if for_you
                     else "coalesce(best.sim * (0.5 + 0.5 * best.gap), 0)::double precision")
    params: dict = {"since": datetime.now(timezone.utc) - timedelta(days=days), "limit": limit + 1, "track": track}
    inner = ["a.duplicate_of is null", "coalesce(a.published_at, a.fetched_at) >= %(since)s", "s.track = %(track)s"]
    outer = []
    if category:
        inner.append("s.category = %(category)s")
        params["category"] = category
    if skill:
        outer.append("best_skill = %(skill)s")
        params["skill"] = skill
    if min_strength:
        # Precision knob: drop articles whose best-skill similarity is below the chosen bucket's threshold.
        params["min_sim"] = settings.match_strong if min_strength == "strong" else settings.match_moderate
        outer.append("similarity >= %(min_sim)s")
    if cursor:
        params["ck"], params["cid"] = _decode(cursor, sort)
        outer.append(f"({key}, id) < (%(ck)s::{'timestamptz' if sort == 'recent' else 'double precision'}, %(cid)s::uuid)")
    sql = f"""
      select * from (
        select a.id, a.title, a.url, a.summary, a.ai_summary, a.engagement,
               coalesce(a.published_at, a.fetched_at) as ts,
               s.slug as source_slug, s.name as source_name, s.category,
               best.slug as best_skill, best.name as best_skill_name, best.sim::double precision as similarity,
               {relevance_sql} as relevance,
               array(select distinct s2.name from articles d join sources s2 on s2.id = d.source_id
                     where d.duplicate_of = a.id order by s2.name) as also_covered_by
        from articles a join sources s on s.id = a.source_id
        left join lateral (
          select k.slug, k.name, 1 - (a.embedding <=> k.embedding) as sim,
                 greatest(0, least(1, (k.target - k.level) / greatest(k.target, 1))) as gap
          from skills k where k.embedding is not null and a.embedding is not null and k.track = %(track)s
          order by a.embedding <=> k.embedding limit 1
        ) best on true
        where {' and '.join(inner)}
      ) f
      {'where ' + ' and '.join(outer) if outer else ''}
      order by {key} desc, id desc limit %(limit)s"""
    if for_you:
        params["lvec"] = level_vec
    rows = await (await conn.execute(sql, params)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{
        "id": r["id"], "title": r["title"], "url": r["url"], "source_slug": r["source_slug"], "source_name": r["source_name"],
        "category": r["category"], "published_at": r["ts"], "summary": r["summary"], "ai_summary": r["ai_summary"],
        "engagement": r["engagement"], "relevance": round(r["relevance"], 4), "also_covered_by": r["also_covered_by"],
        "skill_match": None if r["best_skill"] is None else {
            "skill_slug": r["best_skill"], "skill_name": r["best_skill_name"], "similarity": round(r["similarity"], 4),
            "strength": _strength(r["similarity"], settings)},
    } for r in rows]
    nxt = _encode(sort, rows[-1]["ts"] if sort == "recent" else rows[-1]["relevance"], rows[-1]["id"]) if more and rows else None
    return {"items": items, "next_cursor": nxt}


# ----------------------------------------------------------------------------------------------- Phase 11: Bitcoin
_BTC_LABELS = {"1d": "Tomorrow", "3d": "3 Days", "7d": "7 Days", "14d": "2 Weeks"}


async def fetch_bitcoin_latest(conn: AsyncConnection, news_limit: int = 25) -> dict:
    """Latest spot price, latest forecast reshaped into a `horizons` list (so the UI just maps over it), recent news."""
    from app.bitcoin.technicals import HORIZONS

    price = await (await conn.execute(
        "select price_usd, source, fetched_at from bitcoin_prices order by fetched_at desc limit 1")).fetchone()
    pred = await (await conn.execute("select * from bitcoin_predictions order by created_at desc limit 1")).fetchone()
    news = await (await conn.execute(
        """select id, source_name, title, url, summary, btc_relevant, published_at from bitcoin_news
           order by published_at desc limit %s""", (news_limit,))).fetchall()
    prediction = None
    if pred:
        current = float(pred["current_price"])
        inputs = pred["inputs"] or {}
        sigma = (inputs.get("technicals") or {}).get("one_sigma_move") or {}
        made_on = pred["created_at"].date()
        prediction = {
            **{k: pred[k] for k in ("id", "created_at", "model", "trigger", "sentiment_score", "sentiment_summary", "technical_summary")},
            "current_price": current,
            "horizons": [{
                "key": key, "label": _BTC_LABELS[key], "days": days, "target_date": made_on + timedelta(days=days),
                "predicted_price": float(pred[f"predicted_price_{key}"]),
                "change_pct": round((float(pred[f"predicted_price_{key}"]) / current - 1) * 100, 2),
                "confidence": pred[f"confidence_{key}"], "reasoning": pred[f"reasoning_{key}"], "one_sigma_usd": sigma.get(key),
            } for key, days in HORIZONS],
            "technicals": inputs.get("technicals") or {},
            "history": inputs.get("history") or [],
            "cited_news": [{k: n[k] for k in ("ref", "title", "url", "source")} for n in inputs.get("news") or []],
        }
    run = await (await conn.execute(
        """select id, trigger, status, error, created_at, started_at, finished_at,
                  (status = 'queued' and created_at < now() - interval '2 minutes') as stalled
           from bitcoin_runs order by created_at desc limit 1""")).fetchone()
    return {"price": dict(price) | {"price_usd": float(price["price_usd"])} if price else None, "prediction": prediction, "news": news, "run": run}


async def active_bitcoin_run(conn: AsyncConnection) -> dict | None:
    """A run that is genuinely in flight. Queued rows older than 2 minutes and running rows older than 15 are treated as
    dead (no worker, or a crashed process), so they can't block the button forever."""
    return await (await conn.execute(
        """select id from bitcoin_runs where (status = 'queued' and created_at > now() - interval '2 minutes')
              or (status = 'running' and started_at > now() - interval '15 minutes') order by created_at desc limit 1""")).fetchone()


async def create_bitcoin_run(conn: AsyncConnection, trigger: str) -> UUID:
    return (await (await conn.execute("insert into bitcoin_runs (trigger) values (%s) returning id", (trigger,))).fetchone())["id"]


async def fail_bitcoin_run(conn: AsyncConnection, run_id: UUID, error: str) -> None:
    await conn.execute("update bitcoin_runs set status = 'failed', error = %s, finished_at = now() where id = %s", (error[:1000], run_id))


async def bitcoin_generated_recently(conn: AsyncConnection, minutes: int) -> bool:
    row = await (await conn.execute(
        "select exists(select 1 from bitcoin_predictions where created_at > now() - make_interval(mins => %s)) as e", (minutes,))).fetchone()
    return row["e"]


# ----------------------------------------------------------------------------------------------- learning path
async def submit_quiz(conn: AsyncConnection, task_id: UUID, answers: list[int]) -> dict:
    """Grade the reading quiz. Pass (>= pass_percent) completes the reading, which unlocks the hands-on exercise.
    A failed attempt says WHICH answers were wrong but not the right options: the fix is in the chapter, not in guessing."""
    row = await (await conn.execute(
        """select t.kind, t.quiz, t.quiz_attempts, t.completed_at, b.track from daily_tasks t
           join daily_briefings b on b.id = t.briefing_id where t.id=%s for update of t""", (task_id,))).fetchone()
    if row is None:
        raise SubmissionRejected(404, "task not found")
    pass_percent = lv.ladder(row["track"]).pass_percent
    if row["kind"] != "reading" or not row["quiz"]:
        raise SubmissionRejected(409, "this task has no quiz")
    if row["completed_at"] is not None:
        raise SubmissionRejected(409, "you already passed this quiz")
    questions = row["quiz"]
    try:
        correct, results = lv.grade_choices(questions, answers, reveal=False)
    except ValueError as exc:
        raise SubmissionRejected(422, str(exc)) from None
    mark = lv.pass_mark(len(questions), pass_percent)
    passed = correct >= mark
    if passed:
        _, results = lv.grade_choices(questions, answers, reveal=True)
    first_try = row["quiz_attempts"] == 0
    await conn.execute("update daily_tasks set quiz_attempts = quiz_attempts + 1, quiz_score = %s where id = %s", (correct, task_id))
    completion = None
    if passed:
        completion = await complete_task(conn, task_id, via_quiz=True, xp=lv.XP_QUIZ + (lv.XP_QUIZ_FIRST_TRY if first_try else 0))
    return {"passed": passed, "correct": correct, "total": len(questions), "pass_mark": mark, "results": results,
            "attempts": row["quiz_attempts"] + 1, "completion": completion}


def _tz_day(col: str) -> str:
    return f"({col} at time zone %(tz)s)::date"


async def _level_state(conn: AsyncConnection, today: date, tz: str, lad: lv.Ladder, track: str = "ai") -> dict:
    p = await (await conn.execute("select current_level, placement_taken_at from learner_progress where track = %s", (track,))).fetchone()
    if p is None:  # the migration inserts the rows; recreate one if someone deleted it
        await conn.execute("insert into learner_progress (id, track) values ((select coalesce(max(id), 0) + 1 from learner_progress), %s) "
                           "on conflict do nothing", (track,))
        p = {"current_level": 1, "placement_taken_at": None}
    level = lad.level(p["current_level"])
    done = (await (await conn.execute(
        "select count(*) as n from daily_briefings where level = %s and track = %s and completed_at is not null", (level.index, track))).fetchone())["n"]
    attempts = (await (await conn.execute(
        f"select count(*) as n from exams where kind = 'level' and track = %(t)s and level = %(l)s and submitted_at is not null "
        f"and {_tz_day('submitted_at')} = %(d)s", {"l": level.index, "t": track, "tz": tz, "d": today})).fetchone())["n"]
    completions = await (await conn.execute("select level, via, score, total, completed_at from level_completions where track = %s", (track,))).fetchall()
    return {"p": p, "level": level, "done": done, "attempts_today": attempts, "completions": {c["level"]: c for c in completions}}


async def fetch_progress(conn: AsyncConnection, today: date, tz: str, lad: lv.Ladder, cooldown_hours: int, track: str = "ai") -> dict:
    st = await _level_state(conn, today, tz, lad, track)
    level, done, comps = st["level"], st["done"], st["completions"]
    graduated = level.index == lad.levels[-1].index and level.index in comps
    xp = await (await conn.execute(
        f"select coalesce(sum(amount), 0) as total, coalesce(sum(amount) filter (where {_tz_day('at')} = %(d)s), 0) as today from xp_events",
        {"tz": tz, "d": today})).fetchone()
    days = await (await conn.execute(
        "select day from daily_briefings where completed_at is not null and day > %s", (today - timedelta(days=400),))).fetchall()
    last = await (await conn.execute(
        """select correct, total, passed, submitted_at from exams where kind = 'level' and track = %s and level = %s and submitted_at is not null
           order by submitted_at desc limit 1""", (track, level.index))).fetchone()
    open_exam = await (await conn.execute(
        """select id from exams where kind = 'level' and track = %s and level = %s and submitted_at is null
           and created_at > now() - make_interval(hours => %s) order by created_at desc limit 1""", (track, level.index, cooldown_hours))).fetchone()
    placement_today = (await (await conn.execute(
        f"select exists(select 1 from exams where kind = 'placement' and track = %(t)s and submitted_at is not null "
        f"and {_tz_day('submitted_at')} = %(d)s) as e", {"tz": tz, "d": today, "t": track})).fetchone())["e"]

    def status(i: int) -> str:
        return "completed" if i in comps else "current" if i == level.index else "locked"

    return {
        "track": track, "track_label": lv.TRACK_LABEL[track],
        "level": {**level.model_dump(), "tier_name": lad.tier_name(level.tier), "lessons_required": level.lessons_required},
        "levels_total": len(lad.levels), "lessons_done": done, "graduated": graduated,
        "exam": {"unlocked": done >= level.lessons_required and not graduated, "attempts_today": st["attempts_today"],
                 "attempts_max": lad.exam_attempts_per_day, "pass_percent": lad.pass_percent, "questions": lad.exam_questions,
                 "last": dict(last) if last else None, "open_exam_id": open_exam["id"] if open_exam else None},
        "xp_total": int(xp["total"]), "xp_today": int(xp["today"]),
        "streak_days": lv.streak([d["day"] for d in days], today),
        "knowledge_score": lv.knowledge_score(len(comps), len(lad.levels)),
        "placement": {"taken": st["p"]["placement_taken_at"] is not None, "available_today": not placement_today},
        "path": [{**x.model_dump(), "tier_name": lad.tier_name(x.tier), "status": status(x.index),
                  "via": comps[x.index]["via"] if x.index in comps else None,
                  "score": comps[x.index]["score"] if x.index in comps else None,
                  "total": comps[x.index]["total"] if x.index in comps else None} for x in lad.levels],
    }


def _exam_out(row: dict, lad: lv.Ladder) -> dict:
    total = row["total"]
    return {"id": row["id"], "kind": row["kind"], "level": row["level"], "track": row["track"],
            "level_title": lad.level(row["level"]).title if row["level"] else None,
            "questions": lv.public_questions(row["questions"]), "total": total,
            "pass_mark": lv.pass_mark(total, lad.pass_percent) if row["kind"] == "level" else None}


async def prepare_exam(conn: AsyncConnection, kind: str, today: date, tz: str, lad: lv.Ladder, cooldown_hours: int,
                       placement_questions: list[dict] | None = None, track: str = "ai") -> dict:
    """Either an exam to serve now ({"exam": ...}) or, for a level exam that must be written by the LLM, what to write
    ({"generate": level, "titles": [...]}). The slow LLM call happens OUTSIDE any transaction (see routes)."""
    if kind == "placement":
        st = await _level_state(conn, today, tz, lad, track)
        taken_today = (await (await conn.execute(
            f"select exists(select 1 from exams where kind='placement' and track = %(t)s and submitted_at is not null "
            f"and {_tz_day('submitted_at')} = %(d)s) as e", {"tz": tz, "d": today, "t": track})).fetchone())["e"]
        if taken_today:
            raise SubmissionRejected(429, "you already took the placement exam today; try again tomorrow")
        row = await (await conn.execute(
            "insert into exams (kind, track, questions, total) values ('placement', %s, %s, %s) returning *",
            (track, Jsonb(placement_questions), len(placement_questions)))).fetchone()
        return {"exam": _exam_out(row, lad) | {"current_level": st["level"].index}}
    st = await _level_state(conn, today, tz, lad, track)
    level, done = st["level"], st["done"]
    if level.index == lad.levels[-1].index and level.index in st["completions"]:
        raise SubmissionRejected(409, "you have completed the whole path")
    if done < level.lessons_required:
        raise SubmissionRejected(409, f"finish {level.lessons_required - done} more lesson(s) on this level to unlock its exam")
    if st["attempts_today"] >= lad.exam_attempts_per_day:
        raise SubmissionRejected(429, f"{lad.exam_attempts_per_day} exam attempts used today; review today's lesson and try again tomorrow")
    row = await (await conn.execute(
        """select * from exams where kind = 'level' and track = %s and level = %s and submitted_at is null
           and created_at > now() - make_interval(hours => %s) order by created_at desc limit 1""", (track, level.index, cooldown_hours))).fetchone()
    if row:
        return {"exam": _exam_out(row, lad)}
    titles = await (await conn.execute(
        """select t.title from daily_tasks t join daily_briefings b on b.id = t.briefing_id
           where b.level = %s and b.track = %s and t.kind = 'reading' order by b.day desc limit 8""", (level.index, track))).fetchall()
    return {"generate": level.index, "titles": [t["title"] for t in titles]}


async def store_level_exam(conn: AsyncConnection, level: int, questions: list[dict], lad: lv.Ladder, track: str = "ai") -> dict:
    row = await (await conn.execute(
        "insert into exams (kind, track, level, questions, total) values ('level', %s, %s, %s, %s) returning *",
        (track, level, Jsonb(questions), len(questions)))).fetchone()
    return _exam_out(row, lad)


async def submit_exam(conn: AsyncConnection, exam_id: UUID, answers: list[int]) -> dict:
    exam = await (await conn.execute("select * from exams where id = %s for update", (exam_id,))).fetchone()
    if exam is None:
        raise SubmissionRejected(404, "exam not found")
    track = exam["track"]  # the exam knows its own track: its ladder and placement rules come from there
    lad, tier_pass = lv.ladder(track), lv.placement_bank(track).tier_pass
    if exam["submitted_at"] is not None:
        raise SubmissionRejected(409, "this exam was already submitted; start a new one")
    questions = exam["questions"]
    try:
        correct, results = lv.grade_choices(questions, answers, reveal=True)
    except ValueError as exc:
        raise SubmissionRejected(422, str(exc)) from None
    detailed = [{"question": q["question"], "options": q["options"], **r} for q, r in zip(questions, results)]
    prog = await (await conn.execute("select current_level from learner_progress where track = %s for update", (track,))).fetchone()
    before = prog["current_level"] if prog else 1
    after, xp, placement, passed = before, 0, None, None
    if exam["kind"] == "placement":
        placement = lv.place(questions, answers, tier_pass, lad)
        after = max(before, placement["start_level"])
        for i in range(1, placement["start_level"]):
            await conn.execute("insert into level_completions (track, level, via) values (%s, %s, 'placement') on conflict (track, level) do nothing",
                               (track, i))
        await conn.execute("update learner_progress set current_level = %s, placement_taken_at = now(), updated_at = now() where track = %s",
                           (after, track))
    else:
        mark = lv.pass_mark(len(questions), lad.pass_percent)
        passed = correct >= mark
        if passed:
            await conn.execute(
                """insert into level_completions (track, level, via, score, total) values (%s, %s, 'exam', %s, %s)
                   on conflict (track, level) do update set via = 'exam', score = excluded.score, total = excluded.total, completed_at = now()""",
                (track, exam["level"], correct, len(questions)))
            after = max(before, min(exam["level"] + 1, lad.levels[-1].index))
            await conn.execute("update learner_progress set current_level = %s, updated_at = now() where track = %s", (after, track))
            xp = lv.XP_EXAM_PASS + correct
            await award_xp(conn, xp, f"level {exam['level']} exam passed", exam_id=exam_id)
    await conn.execute(
        "update exams set answers = %s, results = %s, correct = %s, passed = %s, submitted_at = now() where id = %s",
        (Jsonb(answers), Jsonb(detailed), correct, passed, exam_id))
    return {"id": exam_id, "kind": exam["kind"], "track": track, "level": exam["level"], "correct": correct, "total": len(questions),
            "passed": passed, "pass_mark": lv.pass_mark(len(questions), lad.pass_percent) if exam["kind"] == "level" else None,
            "results": detailed, "previous_level": before, "new_level": after,
            "new_level_title": lad.level(after).title, "xp": xp, "placement": placement}


async def level_query_vector(conn: AsyncConnection, lad: lv.Ladder, track: str = "ai") -> tuple[dict, object | None]:
    """The Intelligence Feed's "For you" query: the current level's own embedding, else its skill's embedding."""
    p = await (await conn.execute("select current_level from learner_progress where track = %s", (track,))).fetchone()
    level = lad.level(p["current_level"] if p else 1)
    row = await (await conn.execute(
        """select coalesce((select embedding from level_embeddings where level = %s and track = %s),
                           (select embedding from skills where slug = %s)) as v""", (level.index, track, level.skill))).fetchone()
    return {"index": level.index, "title": level.title, "tier_name": lad.tier_name(level.tier), "skill": level.skill}, row["v"] if row else None
