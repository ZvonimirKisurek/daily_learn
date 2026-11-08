"""Async SQL for the API. Every function takes the request's connection; none opens its own transaction."""
from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.config import Settings
from app.curriculum.skills import apply_delta, compute_delta


class BadCursor(ValueError):
    pass


class SubmissionRequired(Exception):
    """A hands-on task cannot be ticked off: it is completed only by a PASSING graded submission."""


class SubmissionRejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


TASK_COLS = ("id, kind, skill_slug, difficulty, title, completed_at, points_awarded, attempt_count, evaluation_score, "
             "evaluation_passed, (reading_content is not null) as has_content")


# ----------------------------------------------------------------------------------------------- skills
async def fetch_skills(conn: AsyncConnection) -> list[dict]:
    skills = await (await conn.execute("select slug, name, level, target from skills order by name")).fetchall()
    hist = await (await conn.execute(
        """select s.slug, h.level, h.recorded_at, h.delta from skill_history h join skills s on s.id = h.skill_id
           where h.recorded_at > now() - interval '30 days' order by h.recorded_at""")).fetchall()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    out = []
    for s in skills:
        points = [h for h in hist if h["slug"] == s["slug"]]
        out.append({
            "slug": s["slug"], "name": s["name"], "level": round(s["level"], 2), "target": s["target"],
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


async def fetch_today(conn: AsyncConnection, day: date) -> dict:
    b = await (await conn.execute("select id, day, completed_at, payload, theme_id from daily_briefings where day=%s", (day,))).fetchone()
    week_start = day - timedelta(days=day.weekday())
    t = await (await conn.execute("select id, week_start, payload from weekly_themes where week_start=%s", (week_start,))).fetchone()
    return {
        "date": day,
        "state": "ready" if b else "pending",
        "briefing": None if b is None else {"id": b["id"], "day": b["day"], "completed_at": b["completed_at"],
                                            "payload": b["payload"], "tasks": await _tasks(conn, b["id"])},
        "weekly_theme": None if t is None else {"id": t["id"], "week_start": t["week_start"], "theme": t["payload"]},
        "skills": await fetch_skills(conn),
    }


async def list_briefing_days(conn: AsyncConnection) -> list[date]:
    """Every day that has a stored briefing, oldest first. `day` is the primary key, so this is an index scan."""
    rows = await (await conn.execute("select day from daily_briefings order by day")).fetchall()
    return [r["day"] for r in rows]


async def briefing_exists(conn: AsyncConnection, day: date) -> bool:
    return (await (await conn.execute("select 1 from daily_briefings where day=%s", (day,))).fetchone()) is not None


# ----------------------------------------------------------------------------------------------- completion
async def complete_task(conn: AsyncConnection, task_id: UUID, *, via_evaluation: bool = False) -> dict | None:
    """Mark a task done and move its skill - atomically, idempotently, and safe under concurrency.

    * The conditional UPDATE (`completed_at is null`) is the idempotency guard. Two simultaneous requests
      both try it; Postgres lets one proceed and makes the other WAIT on the row lock, then re-evaluates the
      condition, finds it false, and updates zero rows. Exactly one request wins and awards points.
    * `select ... for update` on the skill row serialises read-modify-write of the level (two different tasks
      on the same skill completed at the same instant must not overwrite each other's increment).
    * All of this runs in the request's transaction (Conn dependency): it commits together or not at all.
    """
    kind_row = await (await conn.execute("select kind from daily_tasks where id=%s", (task_id,))).fetchone()
    if kind_row is not None and kind_row["kind"] == "hands_on" and not via_evaluation:
        raise SubmissionRequired  # hands-on work is completed by a passing graded submission, never by a tick
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
    remaining = (await (await conn.execute(
        "select count(*) as n from daily_tasks where briefing_id=%s and completed_at is null", (task["briefing_id"],))).fetchone())["n"]
    if remaining == 0:
        await conn.execute("update daily_briefings set completed_at=now() where id=%s", (task["briefing_id"],))
    return await _completion(conn, task_id, task["skill_slug"], delta, new_level, remaining == 0, False)


async def _completion(conn, task_id, slug, delta, level, briefing_done, already) -> dict:
    task = await (await conn.execute(f"select {TASK_COLS} from daily_tasks where id=%s", (task_id,))).fetchone()
    return {"task": {**task, "completed": task["completed_at"] is not None},
            "skill": {"slug": slug, "level": round(level, 3), "delta": delta},
            "briefing_completed": briefing_done, "already_completed": already}


# ----------------------------------------------------------------------------------------------- task detail + grading
async def get_task_detail(conn: AsyncConnection, task_id: UUID, max_attempts: int) -> dict | None:
    row = await (await conn.execute(
        """select t.id, t.kind, t.skill_slug, t.difficulty, t.title, t.completed_at, t.points_awarded, t.attempt_count,
                  t.evaluation_score, t.evaluation_passed, (t.reading_content is not null) as has_content,
                  t.reading_content, t.notes, t.user_submission, t.evaluation_result, b.day, b.payload
           from daily_tasks t join daily_briefings b on b.id = t.briefing_id where t.id = %s""", (task_id,))).fetchone()
    if row is None:
        return None
    payload, kind = row["payload"], row["kind"]
    task = {k: row[k] for k in ("id", "kind", "skill_slug", "difficulty", "title", "completed_at", "points_awarded", "attempt_count",
                                "evaluation_score", "evaluation_passed", "has_content")}
    task["completed"] = row["completed_at"] is not None
    return {
        "task": task, "day": row["day"], "attempts_max": max_attempts, "attempts_used": row["attempt_count"],
        "reading": {**payload["morning_reading"], "content": row["reading_content"]} if kind == "reading" else None,
        "hands_on": payload["hands_on_task"] if kind == "hands_on" else None,
        "insight": payload["market_insight"] if kind == "insight" else None,
        "notes": row["notes"], "submission": row["user_submission"], "evaluation": row["evaluation_result"],
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
    completion = await complete_task(conn, task_id, via_evaluation=True) if evaluation["passed"] else None
    task = await (await conn.execute(f"select {TASK_COLS} from daily_tasks where id=%s", (task_id,))).fetchone()
    done = await (await conn.execute(
        "select b.completed_at is not null as d from daily_tasks t join daily_briefings b on b.id=t.briefing_id where t.id=%s", (task_id,))).fetchone()
    return {"task": {**task, "completed": task["completed_at"] is not None}, "evaluation": evaluation, "passed": evaluation["passed"],
            "skill": completion["skill"] if completion and not completion["already_completed"] else None,
            "briefing_completed": done["d"], "attempts_used": task["attempt_count"], "attempts_max": max_attempts}


async def briefing_exists(conn: AsyncConnection, day: date) -> bool:
    return await (await conn.execute("select 1 from daily_briefings where day=%s", (day,))).fetchone() is not None


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
    limit: int, cursor: str | None, days: int, min_strength: str | None = None,
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
    params: dict = {"since": datetime.now(timezone.utc) - timedelta(days=days), "limit": limit + 1}
    inner = ["a.duplicate_of is null", "coalesce(a.published_at, a.fetched_at) >= %(since)s"]
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
               coalesce(best.sim * (0.5 + 0.5 * best.gap), 0)::double precision as relevance,
               array(select distinct s2.name from articles d join sources s2 on s2.id = d.source_id
                     where d.duplicate_of = a.id order by s2.name) as also_covered_by
        from articles a join sources s on s.id = a.source_id
        left join lateral (
          select k.slug, k.name, 1 - (a.embedding <=> k.embedding) as sim,
                 greatest(0, least(1, (k.target - k.level) / greatest(k.target, 1))) as gap
          from skills k where k.embedding is not null and a.embedding is not null
          order by a.embedding <=> k.embedding limit 1
        ) best on true
        where {' and '.join(inner)}
      ) f
      {'where ' + ' and '.join(outer) if outer else ''}
      order by {key} desc, id desc limit %(limit)s"""
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
    return {"price": dict(price) | {"price_usd": float(price["price_usd"])} if price else None, "prediction": prediction, "news": news}


async def bitcoin_generated_recently(conn: AsyncConnection, minutes: int) -> bool:
    row = await (await conn.execute(
        "select exists(select 1 from bitcoin_predictions where created_at > now() - make_interval(mins => %s)) as e", (minutes,))).fetchone()
    return row["e"]
