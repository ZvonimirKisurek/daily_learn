"""SQL for the curriculum engine. The backend connects with a privileged role, so it bypasses RLS."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
from psycopg.types.json import Jsonb

from app.curriculum.types import Candidate, Skill
from app.db import get_pool


def _vec(v) -> np.ndarray:
    """pgvector's psycopg loader returns its own `Vector` wrapper; convert to a float32 ndarray."""
    return (v.to_numpy() if hasattr(v, "to_numpy") else np.asarray(v)).astype(np.float32)


def _skill(r: dict) -> Skill:
    return Skill(r["slug"], r["name"], float(r["level"]), float(r["target"]), r["description"],
                 None if r["embedding"] is None else _vec(r["embedding"]))


def load_skills(track: str = "ai") -> list[Skill]:
    with get_pool().connection() as conn:
        return [_skill(r) for r in conn.execute("select * from skills where track = %s order by name", (track,)).fetchall()]


def set_skill_embedding(slug: str, vec: list[float]) -> None:
    with get_pool().connection() as conn:
        conn.execute("update skills set embedding=%s where slug=%s", (np.asarray(vec, dtype=np.float32), slug))


def fetch_candidates(window_days: int, track: str = "ai") -> list[Candidate]:
    """Canonical (non-duplicate) articles from the last N days that have an embedding."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            select a.id, a.title, a.summary, a.url, coalesce(a.published_at, a.fetched_at) as ts, a.engagement,
                   a.embedding, s.slug as source_slug, s.name as source_name, s.category, s.weight
            from articles a join sources s on s.id = a.source_id
            where a.duplicate_of is null and a.embedding is not null and coalesce(a.published_at, a.fetched_at) >= %s
              and s.track = %s
            """,
            (since, track),
        ).fetchall()
    return [
        Candidate(str(r["id"]), r["title"], r["summary"] or "", r["url"], r["ts"], r["source_slug"], r["source_name"],
                  r["category"], float(r["weight"]), r["engagement"], _vec(r["embedding"]))
        for r in rows
    ]


def get_weekly_theme(week_start: date) -> dict | None:
    with get_pool().connection() as conn:
        return conn.execute("select * from weekly_themes where week_start=%s", (week_start,)).fetchone()


def save_weekly_theme(week_start: date, payload: dict, replace: bool = False) -> str:
    with get_pool().connection() as conn:
        if replace:
            conn.execute("delete from weekly_themes where week_start=%s", (week_start,))
        row = conn.execute(
            """insert into weekly_themes (week_start, title, rationale, target_skills, payload)
               values (%s,%s,%s,%s,%s) returning id""",
            (week_start, payload["title"], payload["rationale"], payload["focus_skills"], Jsonb(payload)),
        ).fetchone()
    return str(row["id"])


def get_briefing(day: date, track: str = "ai") -> dict | None:
    with get_pool().connection() as conn:
        return conn.execute("select * from daily_briefings where day=%s and track=%s", (day, track)).fetchone()


def save_briefing(day: date, theme_id: str | None, payload: dict, tasks: list[dict], replace: bool = False, level: int | None = None,
                  track: str = "ai") -> str:
    """One transaction: the briefing row and its three task rows appear together or not at all."""
    with get_pool().connection() as conn:
        if replace:
            conn.execute("delete from daily_briefings where day=%s and track=%s", (day, track))  # cascades to daily_tasks
        bid = conn.execute(
            "insert into daily_briefings (day, theme_id, payload, level, track) values (%s,%s,%s,%s,%s) returning id",
            (day, theme_id, Jsonb(payload), level, track),
        ).fetchone()["id"]
        for t in tasks:
            conn.execute(
                """insert into daily_tasks (briefing_id, kind, skill_slug, difficulty, title, reading_content, evaluation_rubric, quiz)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (bid, t["kind"], t["skill_slug"], t["difficulty"], t["title"], t.get("reading_content"),
                 Jsonb(t["evaluation_rubric"]) if t.get("evaluation_rubric") else None, Jsonb(t["quiz"]) if t.get("quiz") else None),
            )
    return str(bid)


def learner_level(before: date, track: str = "ai") -> tuple[int, int]:
    """(current level, lessons already GENERATED on it for days before `before`). The count picks the next topic, so
    consecutive days get consecutive topics even when generated in one go; FINISHED lessons are what unlock the exam."""
    with get_pool().connection() as conn:
        row = conn.execute("select current_level from learner_progress where track = %s", (track,)).fetchone()
        level = row["current_level"] if row else 1
        n = conn.execute("select count(*) as n from daily_briefings where level = %s and day < %s and track = %s", (level, before, track)).fetchone()["n"]
    return level, n


def briefings_between(start: date, end: date, track: str = "ai") -> list[dict]:
    """Full copies (tasks, submissions, notes, grades) of the briefings in a date range: the backup before a regeneration."""
    with get_pool().connection() as conn:
        rows = conn.execute("select id, day, level, payload, completed_at, created_at from daily_briefings "
                            "where day between %s and %s and track = %s order by day", (start, end, track)).fetchall()
        for r in rows:
            r["tasks"] = conn.execute("select * from daily_tasks where briefing_id = %s order by kind", (r["id"],)).fetchall()
    return rows


def delete_briefings_between(start: date, end: date, track: str = "ai") -> int:
    with get_pool().connection() as conn:  # cascades to daily_tasks; XP and skill history keep their rows (task_id -> null)
        return conn.execute("delete from daily_briefings where day between %s and %s and track = %s", (start, end, track)).rowcount


def has_level_embedding(level: int, track: str = "ai") -> bool:
    with get_pool().connection() as conn:
        return conn.execute("select 1 from level_embeddings where level = %s and track = %s", (level, track)).fetchone() is not None


def set_level_embedding(level: int, vec: list[float], track: str = "ai") -> None:
    with get_pool().connection() as conn:
        conn.execute("insert into level_embeddings (level, track, embedding) values (%s, %s, %s) "
                     "on conflict (track, level) do update set embedding = excluded.embedding",
                     (level, track, np.asarray(vec, dtype=np.float32)))


def used_reading_ids(days: int = 30) -> set[str]:
    since = date.today() - timedelta(days=days)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """select jsonb_path_query(payload, '$.morning_reading.inspiration[*].article_uuid') #>> '{}' as aid
               from daily_briefings where day >= %s""", (since,)
        ).fetchall()
    return {r["aid"] for r in rows if r["aid"]}


def recent_work(before: date, days: int = 5, track: str = "ai") -> list[dict]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            """select b.day, t.kind, t.title, t.completed_at is not null as completed
               from daily_tasks t join daily_briefings b on b.id = t.briefing_id
               where b.day < %s and b.day >= %s and b.track = %s order by b.day, t.kind""",
            (before, before - timedelta(days=days), track),
        ).fetchall()
    return [{"day": r["day"].isoformat(), "kind": r["kind"], "title": r["title"], "completed": r["completed"]} for r in rows]
