"""Orchestration: retrieve -> build prompt -> generate (validated) -> enrich with real URLs -> store.

    skills (+embeddings) ─┐
    last 7d articles ─────┼─► rank_articles (top 50) ─► prompt ─► LLM ─► validated JSON ─► + URLs ─► DB
    weekly theme/history ─┘

Both generators are idempotent per week/day (a re-run returns the stored row) so Celery retries and
accidental double-fires never create duplicates or burn tokens. `force=True` regenerates.
"""
from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.curriculum import content_checks as cc, levels, prompts, repository, roadmap as roadmap_mod
from app.curriculum.llm import StructuredLLM
from app.curriculum.ranking import rank_articles
from app.curriculum.schemas import DailyPlanOutput, GroundingContext, LevelExamOutput, WeeklyThemeOutput
from app.curriculum.types import RankedArticle, Skill

log = logging.getLogger(__name__)


class NotEnoughData(RuntimeError):
    pass


def local_now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def upcoming_monday(d: date) -> date:
    """The week the Sunday-night run plans for. On a Monday it plans that same (current) week."""
    return d if d.weekday() == 0 else d + timedelta(days=7 - d.weekday())


def ensure_skill_embeddings(skills: list[Skill], embedder, repo=repository, settings: Settings | None = None) -> list[Skill]:
    """Embed skill descriptions once and cache them in the DB. They are the *query vectors* for retrieval.
    The embedder (and so an OpenAI key) is only needed the first time, when some skill has no stored vector."""
    missing = [s for s in skills if s.embedding is None]
    if missing:
        import numpy as np
        embedder = embedder or _default_embedder(settings or get_settings())
        vecs = embedder.embed([f"{s.name}: {s.description}" for s in missing])
        for s, v in zip(missing, vecs):
            repo.set_skill_embedding(s.slug, v)
            s.embedding = np.asarray(v, dtype=np.float32)
    return skills


def _select(skills, settings, repo, *, exclude=None, boost=None) -> list[RankedArticle]:
    cands = repo.fetch_candidates(settings.candidate_window_days)
    if len(cands) < settings.min_candidates:
        raise NotEnoughData(f"only {len(cands)} recent articles; run ingestion first (need {settings.min_candidates})")
    return rank_articles(cands, skills, top_n=settings.prompt_article_count, max_per_source=settings.max_per_source,
                         exclude_ids=exclude, skill_boost=boost)


def _grounding(articles: list[RankedArticle], skills: list[Skill]) -> GroundingContext:
    return GroundingContext(frozenset(a.short_id for a in articles), frozenset(s.slug for s in skills))


def _article_ref(a: RankedArticle) -> dict:
    c = a.candidate
    return {"article_uuid": c.id, "ref_id": a.short_id, "title": c.title, "url": c.url, "source": c.source_name, "category": c.category}  # ref_id = the [A7] the text cites


def generate_weekly_theme(
    *, week_start: date | None = None, force: bool = False, dry_run: bool = False,
    llm: StructuredLLM | None = None, embedder=None, repo=repository, settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    today = local_now(settings).date()
    week_start = week_start or upcoming_monday(today)
    existing = repo.get_weekly_theme(week_start)
    if existing and not force and not dry_run:
        return {"id": str(existing["id"]), "week_start": week_start.isoformat(), "payload": existing["payload"], "cached": True}

    skills = ensure_skill_embeddings(repo.load_skills(), embedder, repo, settings)
    articles = _select(skills, settings, repo)
    active = roadmap_mod.active_week(settings, week_start)
    block = prompts.render_roadmap_week(active[0].title, active[1], len(active[0].weeks)) if active else None
    system = prompts.WEEKLY_SYSTEM + (prompts.ROADMAP_RULES if active else "")
    user = prompts.build_weekly_prompt(week_start, today, skills, articles, settings.candidate_window_days, block)
    if dry_run:
        return {"system": system, "user": user, "articles": len(articles)}

    grounding = _grounding(articles, skills)
    if active:  # the repair loop will make the model fix a plan that drifts from the roadmap
        grounding = GroundingContext(grounding.article_ids, grounding.skill_slugs, frozenset(active[1].focus_skills))
    out = (llm or StructuredLLM(settings)).generate(system=system, user=user, output_model=WeeklyThemeOutput, grounding=grounding,
                                                  temperature=settings.temp_weekly)
    by_id = {a.short_id: a for a in articles}
    payload = out.model_dump()
    if active:
        wk = active[1]
        payload["roadmap"] = {"index": wk.index, "of": len(active[0].weeks), "title": wk.title, "goal": wk.goal,
                              "deliverable": wk.deliverable, "milestones": wk.milestones}
    payload["key_articles"] = [_article_ref(by_id[i]) for i in out.key_article_ids]  # ids -> real URLs, by code
    theme_id = repo.save_weekly_theme(week_start, payload, replace=force)
    log.info("weekly theme saved: %s", out.title)
    return {"id": theme_id, "week_start": week_start.isoformat(), "payload": payload, "cached": False}


def current_lesson(day: date, repo=repository, lad: levels.Ladder | None = None) -> dict:
    """The day's place on the learning path: the current level and its next topic (one topic per day, in order; after
    the last topic, review lessons until the exam is passed). A missed day is not lost: its lesson stays on the calendar
    and can be finished later, and only FINISHED lessons unlock the level exam."""
    lad = lad or levels.ladder()
    index, done = repo.learner_level(day)
    lv = lad.level(index)
    topic, review = levels.lesson_topic(lv, done)
    return {"level": lv.index, "of": len(lad.levels), "tier": lv.tier, "tier_name": lad.tier_name(lv.tier), "title": lv.title,
            "summary": lv.summary, "skill": lv.skill, "topics": lv.topics, "topic": topic, "review": review,
            "lesson_number": done + 1, "lessons_required": lv.lessons_required,
            "earlier_levels": [f"{x.index}. {x.title}" for x in lad.levels if x.index < lv.index]}


def ensure_level_embedding(lesson: dict, embedder, repo=repository, settings: Settings | None = None) -> None:
    """Embed the current level once: it is the query vector of the Intelligence Feed's "For you" sort. Best effort: the
    feed falls back to the level's skill vector, so a failure here must never block the lesson."""
    try:
        if repo.has_level_embedding(lesson["level"]):
            return
        embedder = embedder or _default_embedder(settings or get_settings())
        text = f"{lesson['title']} ({lesson['tier_name']} level): {lesson['summary']} Topics: {'; '.join(lesson['topics'])}"
        repo.set_level_embedding(lesson["level"], embedder.embed([text])[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("could not embed level %s for the feed: %r", lesson["level"], exc)


def generate_daily_briefing(
    *, day: date | None = None, force: bool = False, dry_run: bool = False,
    llm: StructuredLLM | None = None, embedder=None, repo=repository, settings: Settings | None = None,
) -> dict:
    """The daily lesson. The weekly theme is still generated and shown, but the LESSON follows the learning path: the
    learner's current level fixes the topic, the skill and how simply everything is explained."""
    settings = settings or get_settings()
    day = day or local_now(settings).date()
    existing = repo.get_briefing(day)
    if existing and not force and not dry_run:
        return {"id": str(existing["id"]), "day": day.isoformat(), "payload": existing["payload"], "cached": True}

    week_start = monday_of(day)
    theme_row = repo.get_weekly_theme(week_start)
    if theme_row is None:  # Sunday job missed or first run mid-week: self-heal rather than fail
        log.warning("no weekly theme for %s; generating on demand", week_start)
        generate_weekly_theme(week_start=week_start, llm=llm, embedder=embedder, repo=repo, settings=settings, dry_run=dry_run)
        theme_row = repo.get_weekly_theme(week_start)
        if theme_row is None and dry_run:
            raise NotEnoughData("dry run needs a stored weekly theme; run generate-weekly first")

    lesson = current_lesson(day, repo)
    skills = ensure_skill_embeddings(repo.load_skills(), embedder, repo, settings)
    articles = _select(skills, settings, repo, exclude=repo.used_reading_ids(),
                       boost={lesson["skill"]: 1.3})  # bias retrieval toward the level's skill
    user = prompts.build_daily_plan_prompt(day, lesson, skills, articles, repo.recent_work(day), settings.candidate_window_days)
    if dry_run:  # prints stage 1 (the plan); stages 2-3 are written from the plan's output
        return {"system": prompts.DAILY_PLAN_SYSTEM, "user": user, "articles": len(articles)}
    ensure_level_embedding(lesson, embedder, repo, settings)

    llm = llm or StructuredLLM(settings)
    by_id = {a.short_id: a for a in articles}
    window = settings.candidate_window_days

    # Stage 1 - PLAN (structured): reading outline + quiz, hands-on exercise with rubric, essay thesis.
    grounding = _grounding(articles, skills)
    grounding = GroundingContext(grounding.article_ids, grounding.skill_slugs, required_skill=lesson["skill"])
    plan = llm.generate(system=prompts.DAILY_PLAN_SYSTEM, user=user, output_model=DailyPlanOutput,
                        grounding=grounding, temperature=settings.temp_daily)
    reading, hands_on, insight = plan.reading, plan.hands_on_task, plan.market_insight
    inspiration = [by_id[i] for i in reading.inspiration_article_ids]
    evidence = [by_id[i] for i in insight.evidence_article_ids]
    chapter_ids = frozenset(a.short_id for a in inspiration)  # the reading may cite only the articles it was shown
    essay_ids = frozenset(a.short_id for a in evidence)
    min_python = 1 if lesson["level"] >= 5 else 0

    # Stages 2 and 3 are independent given the plan, so they run concurrently (halves the latency).
    def write_chapter() -> str:
        return llm.generate_text(
            system=prompts.CHAPTER_SYSTEM, user=prompts.build_chapter_prompt(reading.model_dump(), lesson, inspiration, window),
            check=lambda t: cc.check_chapter(t, min_words=settings.chapter_min_words, max_words=settings.chapter_max_words,
                                             min_python=min_python, article_ids=chapter_ids),
            purpose="Chapter", temperature=settings.temp_chapter, clean=cc.strip_h1)

    def write_essay() -> str:
        return llm.generate_text(
            system=prompts.INSIGHT_SYSTEM, user=prompts.build_insight_prompt(insight.model_dump(), evidence, window, lesson),
            check=lambda t: cc.check_essay(t, min_words=settings.essay_min_words, max_words=settings.essay_max_words, article_ids=essay_ids),
            purpose="Essay", temperature=settings.temp_chapter, max_tokens=settings.llm_max_tokens, clean=cc.strip_h1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        chapter_f, essay_f = pool.submit(write_chapter), pool.submit(write_essay)
        chapter, essay = chapter_f.result(), essay_f.result()

    rng = random.Random(f"{day}-{lesson['level']}")
    quiz = [levels.shuffle_question(q.model_dump(), rng) for q in reading.quiz]  # answer key: stored server-side only
    payload = {
        "lesson": {k: lesson[k] for k in ("level", "of", "tier", "tier_name", "title", "topic", "review", "lesson_number", "lessons_required")},
        "morning_reading": {
            "chapter_title": reading.chapter_title, "skill_slug": reading.skill_slug, "difficulty": reading.difficulty,
            "learning_goals": reading.learning_goals, "estimated_minutes": reading.estimated_minutes,
            "word_count": cc.count_words(chapter), "section_headings": [s.heading for s in reading.sections],
            "inspiration": [_article_ref(a) for a in inspiration], "quiz_questions": len(quiz),
        },
        "hands_on_task": hands_on.model_dump(),
        "market_insight": {
            "headline": insight.headline, "thesis": insight.thesis, "skill_slug": insight.skill_slug, "body": essay,
            "word_count": cc.count_words(essay), "evidence": [_article_ref(a) for a in evidence],
        },
    }
    tasks = [
        {"kind": "reading", "skill_slug": reading.skill_slug, "difficulty": reading.difficulty, "title": reading.chapter_title,
         "reading_content": chapter, "quiz": quiz},
        {"kind": "hands_on", "skill_slug": hands_on.skill_slug, "difficulty": hands_on.difficulty, "title": hands_on.title,
         "evaluation_rubric": hands_on.rubric.model_dump()},
        {"kind": "insight", "skill_slug": insight.skill_slug, "difficulty": 1, "title": insight.headline},
    ]
    briefing_id = repo.save_briefing(day, str(theme_row["id"]), payload, tasks, replace=force, level=lesson["level"])
    log.info("daily lesson saved for %s: level %d, %r (reading %d words, essay %d words)", day, lesson["level"], lesson["topic"],
             payload["morning_reading"]["word_count"], payload["market_insight"]["word_count"])
    return {"id": briefing_id, "day": day.isoformat(), "payload": payload, "cached": False}


def regenerate_lessons(start: date, end: date, *, llm=None, embedder=None, repo=repository, settings: Settings | None = None,
                       backup_path: str | None = None) -> dict:
    """Remake every daily lesson from `start` to `end` (inclusive) in the learning-path format, oldest first, so the days
    get consecutive topics of the current level. DESTRUCTIVE for those days (tasks, submissions, notes): everything is
    first written to `backup_path` as JSON. XP and skill points already earned are kept."""
    import json
    from pathlib import Path

    from app.curriculum.errors import LLMBudgetExceeded

    settings = settings or get_settings()
    old = repo.briefings_between(start, end)
    if backup_path:
        Path(backup_path).write_text(json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "briefings": old}, default=str, indent=1))
    deleted = repo.delete_briefings_between(start, end)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    made, failed = [], []
    for i, d in enumerate(days):
        for attempt in (1, 2):  # one retry: a flaky API call should not leave a hole in the week
            try:
                out = generate_daily_briefing(day=d, force=True, llm=llm, embedder=embedder, repo=repo, settings=settings)
                made.append({"day": d.isoformat(), "level": out["payload"]["lesson"]["level"], "topic": out["payload"]["lesson"]["topic"]})
                break
            except LLMBudgetExceeded as exc:  # every later day would fail the same way: stop and report
                failed += [{"day": x.isoformat(), "error": f"budget: {exc}"} for x in days[i:]]
                return {"backed_up": len(old), "deleted": deleted, "generated": made, "failed": failed}
            except Exception as exc:  # noqa: BLE001
                log.warning("lesson for %s failed (attempt %d): %r", d, attempt, exc)
                if attempt == 2:
                    failed.append({"day": d.isoformat(), "error": repr(exc)[:300]})
    return {"backed_up": len(old), "deleted": deleted, "generated": made, "failed": failed}


def generate_level_exam(level_index: int, recent_titles: list[str], *, llm, settings: Settings | None = None,
                        lad: levels.Ladder | None = None) -> list[dict]:
    """A fresh exam for one level, written by the LLM, validated, then SHUFFLED by code. Returns the full questions
    (with the answer key) for the caller to store; the API only ever serves the questions."""
    settings = settings or get_settings()
    lad = lad or levels.ladder()
    lv = lad.level(level_index)
    out = llm.generate(
        system=prompts.LEVEL_EXAM_SYSTEM,
        user=prompts.build_level_exam_prompt({**lv.model_dump(), "tier_name": lad.tier_name(lv.tier)}, lad.exam_questions, recent_titles),
        output_model=LevelExamOutput, grounding=GroundingContext(frozenset(), frozenset()), temperature=settings.temp_daily)
    rng = random.Random()
    return [levels.shuffle_question(q.model_dump(), rng) for q in out.questions]


def _default_embedder(settings: Settings):
    from app.ingestion.embeddings import OpenAIEmbedder
    return OpenAIEmbedder(settings)
