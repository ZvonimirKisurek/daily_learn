"""Async SQL for the mastery loop (reviews, retrospective, math drill, gates). Same conventions as app/api/queries.py:
every function takes the request's connection and runs inside the request's transaction."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.config import Settings
from app.curriculum import roadmap as roadmap_mod
from app.curriculum.skills import apply_delta, compute_delta
from app.mastery import mathdrill, srs

CARD_COLS = ("id, source, skill_slug, prompt, due_on, reps, lapses, interval_days, ease, task_id, week_start, last_reviewed_at, "
             "(context <> '') as has_reference")


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Rejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


# ----------------------------------------------------------------------------------------------- catch-up
def is_archived(day: date, completed: bool, today: date, catchup_days: int) -> bool:
    """A past task is read-only once done, or once it falls out of the catch-up window. Unfinished recent lessons stay doable."""
    return day < today and (completed or day < today - timedelta(days=catchup_days))


async def catchup(conn: AsyncConnection, today: date, catchup_days: int) -> list[dict]:
    """Unfinished lessons from past days inside the window, oldest first (do the oldest before it expires)."""
    rows = await (await conn.execute(
        """select t.id, t.kind, t.title, t.skill_slug, t.attempt_count, b.day, b.track, (t.acceptance_tests is not null) as has_tests,
                  (b.payload ? 'lesson') as path_lesson
           from daily_tasks t join daily_briefings b on b.id = t.briefing_id
           where t.completed_at is null and b.day < %s and b.day >= %s
           order by b.day, case t.kind when 'reading' then 1 when 'hands_on' then 2 else 3 end""",
        (today, today - timedelta(days=catchup_days)))).fetchall()
    # learning-path lessons never expire (they count toward the level exam); older-style lessons do
    return [{**dict(r), "expires_on": None if r["path_lesson"] else r["day"] + timedelta(days=catchup_days)} for r in rows]


# ----------------------------------------------------------------------------------------------- gates
async def overdue_count(conn: AsyncConnection, today: date) -> int:
    row = await (await conn.execute("select count(*) as n from review_cards where not suspended and due_on < %s", (today,))).fetchone()
    return row["n"]


async def retro_required_for(conn: AsyncConnection, week_start: date) -> bool:
    """A retrospective is owed for a week that had at least one briefing (a week you never started owes nothing) and that
    started on or after the Monday of the week the mastery loop was installed (mastery_meta)."""
    meta = await (await conn.execute("select installed_at from mastery_meta where id = 1")).fetchone()
    if meta is not None and week_start < monday(meta["installed_at"].date()):
        return False
    row = await (await conn.execute("select 1 from daily_briefings where day between %s and %s limit 1",
                                    (week_start, week_start + timedelta(days=6)))).fetchone()
    return row is not None


async def retro_submitted(conn: AsyncConnection, week_start: date) -> bool:
    return (await (await conn.execute("select 1 from weekly_retros where week_start=%s", (week_start,))).fetchone()) is not None


async def gate_reasons(conn: AsyncConnection, cfg: Settings, today: date, task_day: date | None = None) -> list[str]:
    """Why new work is locked right now (empty list = unlocked).

    * review gate: cards due BEFORE today are a backlog. Today's cards can be done any time today; yesterday's cannot wait.
    * retro gate: the week before the task's week had briefings but no submitted retrospective.
    Reviews, the retrospective and the math drill are never gated: they are how you unlock."""
    reasons = []
    if cfg.review_gate:
        n = await overdue_count(conn, today)
        if n:
            reasons.append(f"{n} review card{'s are' if n != 1 else ' is'} overdue. Answer them in Review first (retrieval practice before new material).")
    if cfg.retro_gate:
        prev = monday(task_day or today) - timedelta(days=7)
        if await retro_required_for(conn, prev) and not await retro_submitted(conn, prev):
            reasons.append(f"Submit the retrospective for the week of {prev.isoformat()} first: evidence for each milestone, "
                           "the teach-back questions, and your decision for this week.")
    return reasons


# ----------------------------------------------------------------------------------------------- reviews
async def due_cards(conn: AsyncConnection, today: date, limit: int) -> list[dict]:
    rows = await (await conn.execute(
        f"select {CARD_COLS} from review_cards where not suspended and due_on <= %s order by due_on, created_at limit %s",
        (today, limit))).fetchall()
    return [dict(r) for r in rows]


async def card(conn: AsyncConnection, card_id: UUID, *, with_context: bool = False, lock: bool = False) -> dict | None:
    cols = CARD_COLS + (", context" if with_context else "")
    return await (await conn.execute(f"select {cols} from review_cards where id=%s{' for update' if lock else ''}", (card_id,))).fetchone()


async def grade_card(conn: AsyncConnection, card_id: UUID, grade: int, answer: str, feedback: dict | None, today: date) -> dict | None:
    row = await card(conn, card_id, lock=True)
    if row is None:
        return None
    state, due = srs.schedule(srs.CardState(row["interval_days"], row["ease"], row["reps"], row["lapses"]), grade, today)
    await conn.execute(
        """update review_cards set interval_days=%s, ease=%s, reps=%s, lapses=%s, due_on=%s, last_reviewed_at=now() where id=%s""",
        (state.interval_days, state.ease, state.reps, state.lapses, due, card_id))
    await conn.execute("insert into review_log (card_id, grade, answer, feedback, interval_days) values (%s,%s,%s,%s,%s)",
                       (card_id, grade, answer, Jsonb(feedback) if feedback else None, state.interval_days))
    remaining = (await (await conn.execute(
        "select count(*) as n from review_cards where not suspended and due_on <= %s", (today,))).fetchone())["n"]
    return {"card": dict(await card(conn, card_id)), "next_due": due, "remaining_due": remaining}


async def suspend_card(conn: AsyncConnection, card_id: UUID, suspended: bool) -> dict | None:
    row = await (await conn.execute(f"update review_cards set suspended=%s where id=%s returning {CARD_COLS}", (suspended, card_id))).fetchone()
    return dict(row) if row else None


async def review_stats(conn: AsyncConnection, today: date) -> dict:
    counts = await (await conn.execute(
        """select count(*) filter (where not suspended and due_on <= %(t)s) as due,
                  count(*) filter (where not suspended and due_on < %(t)s) as overdue,
                  count(*) filter (where not suspended) as active,
                  count(*) filter (where not suspended and due_on > %(t)s and due_on <= %(t)s + 7) as next_7_days
           from review_cards""", {"t": today})).fetchone()
    log = await (await conn.execute(
        """select count(*) as n, count(*) filter (where grade >= 3) as good,
                  count(*) filter (where reviewed_at::date = %s) as today
           from review_log where reviewed_at > now() - interval '30 days'""", (today,))).fetchone()
    return {**dict(counts), "reviewed_today": log["today"], "reviews_30d": log["n"],
            "retention_30d": round(log["good"] / log["n"], 3) if log["n"] else None}


# ----------------------------------------------------------------------------------------------- retrospective
async def retro_view(conn: AsyncConnection, cfg: Settings, week_start: date, today: date) -> dict:
    end = week_start + timedelta(days=6)
    theme = await (await conn.execute("select payload from weekly_themes where week_start=%s", (week_start,))).fetchone()
    payload = (theme or {}).get("payload") or {}
    rm = payload.get("roadmap") or {}
    milestones = rm.get("milestones") or payload.get("learning_objectives") or []
    tasks = await (await conn.execute(
        """select t.kind, t.completed_at, t.points_awarded, t.evaluation_passed, t.attempt_count, t.test_report
           from daily_tasks t join daily_briefings b on b.id=t.briefing_id where b.day between %s and %s""", (week_start, end))).fetchall()
    done = [t for t in tasks if t["completed_at"]]
    by_kind = {k: {"done": sum(1 for t in done if t["kind"] == k), "total": sum(1 for t in tasks if t["kind"] == k)}
               for k in ("reading", "hands_on", "insight")}
    times = sorted(t["completed_at"] for t in done)
    bursts = sum(1 for i in range(len(times) - 2) if times[i + 2] - times[i] <= timedelta(minutes=10))
    teach = await (await conn.execute(
        """select c.id, c.prompt, c.due_on, exists(select 1 from review_log l where l.card_id=c.id) as answered
           from review_cards c where c.source='teachback' and c.week_start=%s order by c.created_at""", (week_start,))).fetchall()
    math = await (await conn.execute(
        "select count(*) as n, count(*) filter (where correct) as correct from math_attempts where day between %s and %s",
        (week_start, end))).fetchone()
    reviews = await (await conn.execute(
        "select count(*) as n, count(*) filter (where grade >= 3) as good from review_log where reviewed_at::date between %s and %s",
        (week_start, end))).fetchone()
    existing = await (await conn.execute("select * from weekly_retros where week_start=%s", (week_start,))).fetchone()
    required = await retro_required_for(conn, week_start)
    blockers = []
    if today < end:
        blockers.append(f"the week is not over: the retrospective opens on Sunday {end.isoformat()}")
    unanswered = [t for t in teach if not t["answered"]]
    if unanswered:
        blockers.append(f"answer the {len(unanswered)} teach-back question(s) from memory in Review first")
    return {
        "week_start": week_start, "week_end": end, "required": required, "submitted": existing is not None,
        "can_submit": not blockers and existing is None, "blockers": blockers,
        "theme_title": payload.get("title"), "milestones": milestones, "deliverable": rm.get("deliverable"),
        "quarter": rm.get("quarter"), "roadmap_week": rm.get("index"),
        "numbers": {"tasks_done": len(done), "tasks_total": len(tasks), "by_kind": by_kind, "click_bursts": bursts,
                    "hands_on_passed": sum(1 for t in tasks if t["kind"] == "hands_on" and t["evaluation_passed"]),
                    "tests_all_passed": sum(1 for t in tasks if (t["test_report"] or {}).get("all_passed")),
                    "math_attempted": math["n"], "math_correct": math["correct"],
                    "reviews": reviews["n"], "review_retention": round(reviews["good"] / reviews["n"], 3) if reviews["n"] else None},
        "teachback": [{"id": t["id"], "prompt": t["prompt"], "due_on": t["due_on"], "answered": t["answered"]} for t in teach],
        "retro": None if existing is None else {k: existing[k] for k in ("submitted_at", "milestone_evidence", "reflection", "decision", "public_artifact_url")},
    }


async def submit_retro(conn: AsyncConnection, cfg: Settings, week_start: date, today: date, body: dict) -> dict:
    view = await retro_view(conn, cfg, week_start, today)
    if view["submitted"]:
        raise Rejected(409, "this week's retrospective was already submitted")
    if view["blockers"]:
        raise Rejected(409, "; ".join(view["blockers"]))
    given = {e["milestone"].strip(): e["evidence"].strip() for e in body["milestone_evidence"]}
    missing = [m for m in view["milestones"] if not given.get(m.strip())]
    if missing:
        raise Rejected(422, f"give evidence (a link, a commit, a number) or write 'not done: <why>' for every milestone; missing: {missing[:3]}")
    q = view.get("quarter") or {}
    if q and view["roadmap_week"] == (q.get("weeks") or [0, 0])[1] and not body.get("public_artifact_url"):
        raise Rejected(422, f"this week closes quarter {q.get('index')}: link the public artifact ({q.get('public_deliverable', '')[:120]}...)")
    evidence = [{"milestone": m, "evidence": given[m.strip()]} for m in view["milestones"]] or body["milestone_evidence"]
    await conn.execute(
        """insert into weekly_retros (week_start, milestone_evidence, reflection, decision, public_artifact_url)
           values (%s,%s,%s,%s,%s)""",
        (week_start, Jsonb(evidence), body["reflection"], body["decision"], body.get("public_artifact_url")))
    return await retro_view(conn, cfg, week_start, today)


# ----------------------------------------------------------------------------------------------- math drill
async def _week_topics(conn: AsyncConnection, cfg: Settings, day: date) -> list[str]:
    row = await (await conn.execute("select payload from weekly_themes where week_start=%s", (monday(day),))).fetchone()
    topics = ((row or {}).get("payload") or {}).get("math_topics")
    if topics:
        return topics
    active = roadmap_mod.active_week(cfg, monday(day))
    if active and active[1].math_topics:
        return active[1].math_topics
    # no roadmap: rotate through every topic, four per week
    k = (day.toordinal() // 7) * 4 % len(mathdrill.TOPICS)
    return (mathdrill.TOPICS * 2)[k:k + 4]


async def _review_topics(conn: AsyncConnection, day: date) -> list[str]:
    """Topics missed in the last 21 days, judged only on attempts BEFORE today, so today's drill is stable all day."""
    rows = await (await conn.execute(
        """select topic from math_attempts where day < %s and day >= %s
           group by topic having bool_or(not correct) order by max(day) desc limit 2""", (day, day - timedelta(days=21)))).fetchall()
    return [r["topic"] for r in rows]


async def math_drill(conn: AsyncConnection, cfg: Settings, day: date) -> dict:
    topics = mathdrill.drill_topics(day, await _week_topics(conn, cfg, day), await _review_topics(conn, day), cfg.math_problems_per_day)
    attempts = {r["idx"]: r for r in await (await conn.execute("select * from math_attempts where day=%s", (day,))).fetchall()}
    problems = []
    for i, topic in enumerate(topics):
        p = mathdrill.problem(day, i, topic)
        a = attempts.get(i)
        problems.append({"idx": i, "topic": topic, "area": mathdrill.GENERATORS[topic][0], "prompt": p.prompt, "hint": p.hint,
                         "attempted": a is not None, "correct": a["correct"] if a else None, "answer_given": a["answer"] if a else None,
                         "expected": p.answer if a else None, "solution": p.solution if a else None})
    done = [p for p in problems if p["attempted"]]
    return {"day": day, "problems": problems, "attempted": len(done), "correct": sum(1 for p in done if p["correct"]), "total": len(problems)}


async def answer_math(conn: AsyncConnection, cfg: Settings, day: date, idx: int, answer: str) -> dict:
    topics = mathdrill.drill_topics(day, await _week_topics(conn, cfg, day), await _review_topics(conn, day), cfg.math_problems_per_day)
    if not 0 <= idx < len(topics):
        raise Rejected(404, "no such problem today")
    given = mathdrill.parse_answer(answer)
    if given is None:
        raise Rejected(422, "enter a number, e.g. 0.25, -3, 1/4 or 25%")
    p = mathdrill.problem(day, idx, topics[idx])
    correct = mathdrill.is_correct(p, given)
    prev = await (await conn.execute("select * from math_attempts where day=%s and idx=%s for update", (day, idx))).fetchone()
    skill = None
    if prev is None:
        points = 0.0
        if correct:  # only a FIRST-try correct answer moves the skill: retries after seeing the solution are practice
            lvl = await (await conn.execute("select level from skills where slug='math-foundations' for update")).fetchone()
            if lvl is not None:
                points = compute_delta("math", 1, float(lvl["level"]))
                new = apply_delta(float(lvl["level"]), points)
                await conn.execute("update skills set level=%s, updated_at=now() where slug='math-foundations'", (new,))
                await conn.execute("insert into skill_history (skill_id, level, delta) select id, %s, %s from skills where slug='math-foundations'",
                                   (new, points))
                skill = {"slug": "math-foundations", "level": round(new, 3), "delta": points}
        await conn.execute("insert into math_attempts (day, idx, topic, answer, correct, points) values (%s,%s,%s,%s,%s,%s)",
                           (day, idx, topics[idx], answer.strip()[:60], correct, points))
    else:
        await conn.execute("update math_attempts set tries = tries + 1 where id=%s", (prev["id"],))
    return {"idx": idx, "correct": correct, "first_attempt": prev is None, "counted_correct": prev["correct"] if prev else correct,
            "expected": p.answer, "solution": p.solution, "skill": skill}


# ----------------------------------------------------------------------------------------------- the dashboard summary
async def status(conn: AsyncConnection, cfg: Settings, today: date) -> dict:
    stats = await review_stats(conn, today)
    prev = monday(today) - timedelta(days=7)
    math_row = await (await conn.execute(
        "select count(*) as n, count(*) filter (where correct) as correct from math_attempts where day=%s", (today,))).fetchone()
    acc = await (await conn.execute(
        "select count(*) as n, count(*) filter (where correct) as c from math_attempts where day > %s", (today - timedelta(days=30),))).fetchone()
    return {
        "date": today, "reviews": stats,
        "gate": {"locked": bool(reasons := await gate_reasons(conn, cfg, today)), "reasons": reasons},
        "retro": {"week_start": prev, "required": await retro_required_for(conn, prev), "submitted": await retro_submitted(conn, prev)},
        "math": {"attempted": math_row["n"], "correct": math_row["correct"], "total": cfg.math_problems_per_day,
                 "accuracy_30d": round(acc["c"] / acc["n"], 3) if acc["n"] else None},
        "catchup": await catchup(conn, today, cfg.catchup_days),
        "generated_at": datetime.now(timezone.utc),
    }
