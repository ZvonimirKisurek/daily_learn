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


def load_skills() -> list[Skill]:
    with get_pool().connection() as conn:
        return [_skill(r) for r in conn.execute("select * from skills order by name").fetchall()]


def set_skill_embedding(slug: str, vec: list[float]) -> None:
    with get_pool().connection() as conn:
        conn.execute("update skills set embedding=%s where slug=%s", (np.asarray(vec, dtype=np.float32), slug))


def fetch_candidates(window_days: int) -> list[Candidate]:
    """Canonical (non-duplicate) articles from the last N days that have an embedding."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            select a.id, a.title, a.summary, a.url, coalesce(a.published_at, a.fetched_at) as ts, a.engagement,
                   a.embedding, s.slug as source_slug, s.name as source_name, s.category, s.weight
            from articles a join sources s on s.id = a.source_id
            where a.duplicate_of is null and a.embedding is not null and coalesce(a.published_at, a.fetched_at) >= %s
            """,
            (since,),
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


def get_briefing(day: date) -> dict | None:
    with get_pool().connection() as conn:
        return conn.execute("select * from daily_briefings where day=%s", (day,)).fetchone()


def save_briefing(day: date, theme_id: str, payload: dict, tasks: list[dict], replace: bool = False, level: int | None = None) -> str:
    """One transaction: the briefing row and its three task rows appear together or not at all."""
    with get_pool().connection() as conn:
        if replace:
            conn.execute("delete from daily_briefings where day=%s", (day,))  # cascades to daily_tasks
        bid = conn.execute(
            "insert into daily_briefings (day, theme_id, payload, level) values (%s,%s,%s,%s) returning id",
            (day, theme_id, Jsonb(payload), level),
        ).fetchone()["id"]
        for t in tasks:
            conn.execute(
                """insert into daily_tasks (briefing_id, kind, skill_slug, difficulty, title, reading_content, evaluation_rubric, quiz)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (bid, t["kind"], t["skill_slug"], t["difficulty"], t["title"], t.get("reading_content"),
                 Jsonb(t["evaluation_rubric"]) if t.get("evaluation_rubric") else None, Jsonb(t["quiz"]) if t.get("quiz") else None),
            )
    return str(bid)


def learner_level() -> tuple[int, int]:
    """(current level, lessons FULLY finished on it). Lessons count only when every task was done."""
    with get_pool().connection() as conn:
        row = conn.execute("select current_level from learner_progress where id = 1").fetchone()
        level = row["current_level"] if row else 1
        done = conn.execute("select count(*) as n from daily_briefings where level = %s and completed_at is not null", (level,)).fetchone()["n"]
    return level, done


def has_level_embedding(level: int) -> bool:
    with get_pool().connection() as conn:
        return conn.execute("select 1 from level_embeddings where level = %s", (level,)).fetchone() is not None


def set_level_embedding(level: int, vec: list[float]) -> None:
    with get_pool().connection() as conn:
        conn.execute("insert into level_embeddings (level, embedding) values (%s, %s) on conflict (level) do update set embedding = excluded.embedding",
                     (level, np.asarray(vec, dtype=np.float32)))


def used_reading_ids(days: int = 30) -> set[str]:
    since = date.today() - timedelta(days=days)
    with get_pool().connection() as conn:
        rows = conn.execute(
            """select jsonb_path_query(payload, '$.morning_reading.inspiration[*].article_uuid') #>> '{}' as aid
               from daily_briefings where day >= %s""", (since,)
        ).fetchall()
    return {r["aid"] for r in rows if r["aid"]}


def recent_work(before: date, days: int = 5) -> list[dict]:
    with get_pool().connection() as conn:
        rows = conn.execute(
            """select b.day, t.kind, t.title, t.completed_at is not null as completed
               from daily_tasks t join daily_briefings b on b.id = t.briefing_id
               where b.day < %s and b.day >= %s order by b.day, t.kind""",
            (before, before - timedelta(days=days)),
        ).fetchall()
    return [{"day": r["day"].isoformat(), "kind": r["kind"], "title": r["title"], "completed": r["completed"]} for r in rows]
