"""Weekly retrospective numbers + an honest click-through detector.

Completing a task is a *behaviour signal*, not proof of learning: a checkbox can be ticked without reading anything.
This module reports the behaviours that separate real work from box-ticking (building vs only reading, ticks
spread over the day vs three ticks in one minute, ticks on the right day vs days later) and the skill movement
the formula granted. The judgement of whether you can DO the thing stays with you: see docs/RETROSPECTIVE.md.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from app.db import get_pool

BURST_MINUTES = 10  # >= 3 completions within this window looks like click-through


def fetch_rows(days: int = 7) -> list[dict]:
    since = date.today() - timedelta(days=days - 1)
    with get_pool().connection() as conn:
        return conn.execute(
            """select b.day, t.kind, t.skill_slug, t.title, t.completed_at, t.points_awarded
               from daily_tasks t join daily_briefings b on b.id = t.briefing_id where b.day >= %s order by b.day, t.kind""", (since,)).fetchall()


def analyze(rows: list[dict]) -> dict:
    total = len(rows)
    done = [r for r in rows if r["completed_at"]]
    by_kind = {k: (sum(1 for r in done if r["kind"] == k), sum(1 for r in rows if r["kind"] == k)) for k in ("reading", "hands_on", "insight")}
    gains: dict[str, float] = {}
    for r in done:
        gains[r["skill_slug"]] = gains.get(r["skill_slug"], 0.0) + float(r["points_awarded"] or 0)

    # click-through signal 1: three or more completions inside a short window (any day)
    times = sorted(r["completed_at"] for r in done)
    burst = any((times[i + 2] - times[i]) <= timedelta(minutes=BURST_MINUTES) for i in range(len(times) - 2))
    burst_windows = sum(1 for i in range(len(times) - 2) if (times[i + 2] - times[i]) <= timedelta(minutes=BURST_MINUTES))
    # click-through signal 2: ticked on a different calendar day than the briefing (UTC dates; approximate)
    late = sum(1 for r in done if isinstance(r["completed_at"], datetime) and r["completed_at"].date() > r["day"])

    days_with_task = sorted({r["day"] for r in done})
    streak, cursor = 0, max((r["day"] for r in rows), default=None)
    while cursor and cursor in days_with_task:
        streak += 1
        cursor -= timedelta(days=1)
    return {"total": total, "done": len(done), "rate": len(done) / total if total else 0.0, "by_kind": by_kind, "gains": gains,
            "burst": burst, "burst_windows": burst_windows, "late_ticks": late, "streak": streak}


def render(a: dict, milestones: list[str] | None = None) -> str:
    if not a["total"]:
        return "no briefings in the last 7 days"
    h_done, h_total = a["by_kind"]["hands_on"]
    L = [f"tasks completed: {a['done']}/{a['total']} ({a['rate']:.0%})   current streak: {a['streak']} day(s)",
         "by kind: " + ", ".join(f"{k} {d}/{t}" for k, (d, t) in a["by_kind"].items()),
         "skill points granted: " + (", ".join(f"{k} +{v:.2f}" for k, v in sorted(a["gains"].items(), key=lambda kv: -kv[1])) or "none"), "", "Signals:"]
    L.append(f"  [{'!' if h_total and h_done / h_total < 0.6 else 'ok'}] hands-on completion {h_done}/{h_total}"
             + ("  -> you are reading more than building; building is where the learning is" if h_total and h_done / h_total < 0.6 else ""))
    L.append(f"  [{'!' if a['burst'] else 'ok'}] ticks clustered: {a['burst_windows']} window(s) of 3+ completions within {BURST_MINUTES} min"
             + ("  -> possible click-through; tick when you finish, not at the end of the day" if a["burst"] else ""))
    L.append(f"  [{'!' if a['late_ticks'] > a['done'] / 2 else 'ok'}] {a['late_ticks']} task(s) ticked on a later day than their briefing")
    if milestones:
        L += ["", "This week's roadmap milestones. For EACH, point at an artifact (commit, notebook, plot, write-up) or it is not done:"]
        L += [f"  [ ] {m}" for m in milestones]
    return "\n".join(L)


def latest_milestones() -> list[str]:
    with get_pool().connection() as conn:
        row = conn.execute("select payload from weekly_themes order by week_start desc limit 1").fetchone()
    return ((row or {}).get("payload") or {}).get("roadmap", {}).get("milestones", [])


def export_progress(path: str | Path) -> dict:
    """Everything irreplaceable (your progress), as portable JSON. Articles are re-fetchable, so they are not exported."""
    q = {
        "skills": "select slug, name, level, target, description, updated_at from skills order by slug",
        "skill_history": "select s.slug, h.level, h.delta, h.recorded_at, h.task_id from skill_history h join skills s on s.id=h.skill_id order by h.recorded_at",
        "weekly_themes": "select week_start, title, rationale, target_skills, payload, created_at from weekly_themes order by week_start",
        "daily_briefings": "select day, payload, completed_at, created_at from daily_briefings order by day",
        "daily_tasks": "select b.day, t.kind, t.skill_slug, t.difficulty, t.title, t.completed_at, t.points_awarded from daily_tasks t join daily_briefings b on b.id=t.briefing_id order by b.day, t.kind",
        # the mastery loop: your memory schedule, your retrospectives and your math record are as irreplaceable as the skills
        "review_cards": "select id, source, skill_slug, prompt, due_on, interval_days, ease, reps, lapses, suspended, created_at from review_cards order by created_at",
        "review_log": "select card_id, reviewed_at, grade, answer, interval_days from review_log order by reviewed_at",
        "weekly_retros": "select * from weekly_retros order by week_start",
        "math_attempts": "select day, idx, topic, answer, correct, tries, points from math_attempts order by day, idx",
    }
    with get_pool().connection() as conn:
        data = {k: conn.execute(sql).fetchall() for k, sql in q.items()}
    Path(path).write_text(json.dumps({"exported_at": datetime.now().isoformat(), **data}, default=str, indent=1))
    return {k: len(v) for k, v in data.items()}
