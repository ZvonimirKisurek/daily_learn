"""Turning generated content into review cards, deterministically.

Three sources, each with a dedupe key so regenerating a briefing or theme never duplicates cards:
  * chapter        - every question in the chapter's '## Check Your Understanding' section. First due tomorrow.
  * teachback      - the week's core_questions (the teach-back test from docs/RETROSPECTIVE.md). Due on the week's Sunday,
                     so the retrospective cannot be submitted until you have answered them from memory.
  * primary_source - the guiding question attached to the day's primary source. Due two days later.

The `context` stored with each card is the reference an AI recall check judges your answer against. It is never the
answer itself shown up front: you answer first, then compare.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

_CYU = re.compile(r"^##\s+.*(check your understanding|self-test|exercises).*$", re.I | re.M)
_NEXT_H2 = re.compile(r"^##\s", re.M)
_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*+])\s+(.+)$")
MAX_CONTEXT = 12000
_NEEDS_OPTIONS = re.compile(r"\b(which of|of the following|these options|which one|which statement|which option)\b", re.I)


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def check_questions(chapter_md: str, limit: int = 6) -> list[str]:
    """The numbered/bulleted items of the 'Check Your Understanding' section (multi-line items are joined)."""
    m = _CYU.search(chapter_md)
    if not m:
        return []
    rest = chapter_md[m.end():]
    nxt = _NEXT_H2.search(rest)
    body = rest[: nxt.start()] if nxt else rest
    items: list[str] = []
    for line in body.splitlines():
        hit = _ITEM.match(line)
        if hit:
            items.append(hit.group(1).strip())
        elif items and line.strip() and not line.lstrip().startswith("#"):
            items[-1] += " " + line.strip()  # continuation of the previous item
    return [q[:1000] for q in items if len(q) >= 5][:limit]


def chapter_cards(*, task_id: str, day: date, skill_slug: str, chapter_md: str) -> list[dict]:
    return [{"source": "chapter", "dedupe_key": _key("chapter", day.isoformat(), q), "task_id": task_id, "week_start": None,
             "skill_slug": skill_slug, "prompt": q, "context": chapter_md[:MAX_CONTEXT], "due_on": day + timedelta(days=1)}
            for q in check_questions(chapter_md)]


def quiz_cards(*, day: date, skill_slug: str, quiz: list[dict], chapter_md: str) -> list[dict]:
    """The lesson's multiple-choice quiz, turned into free-recall cards: the question WITHOUT its options. Recognising
    the right option is much easier than producing it, and producing it is what builds durable memory. The reference
    the answer is checked against is the right option, its explanation, and the lesson text."""
    out = []
    for q in quiz:
        question = (q.get("question") or "").strip()
        if len(question) < 5:
            continue
        right = q["options"][q["answer"]] if q.get("options") and isinstance(q.get("answer"), int) else ""
        if _NEEDS_OPTIONS.search(question) and q.get("options"):  # "Which of the following...?" is meaningless without them
            question += "\n" + "\n".join(f"{'ABCD'[i]}) {o}" for i, o in enumerate(q["options"])) + "\n(Explain why, not only which.)"
        context = f"Correct answer: {right}\nWhy: {q.get('explanation', '')}\n\n--- lesson ---\n{chapter_md}"
        out.append({"source": "chapter", "dedupe_key": _key("quiz", day.isoformat(), question), "task_id": None, "week_start": None,
                    "skill_slug": skill_slug, "prompt": question[:1000], "context": context[:MAX_CONTEXT], "due_on": day + timedelta(days=1)})
    return out


def teachback_cards(*, week_start: date, theme: dict) -> list[dict]:
    focus = (theme.get("focus_skills") or ["classical-ml"])[0]
    context = f"{theme.get('title', '')}\n\n{theme.get('rationale', '')}\n\n{theme.get('seminar_overview', '')}"[:MAX_CONTEXT]
    return [{"source": "teachback", "dedupe_key": _key("teachback", week_start.isoformat(), q), "task_id": None, "week_start": week_start,
             "skill_slug": focus, "prompt": q, "context": context, "due_on": week_start + timedelta(days=6)}
            for q in theme.get("core_questions", [])]


def primary_source_card(*, task_id: str, day: date, skill_slug: str, source: dict) -> dict:
    prompt = f"{source['guiding_question']} (from: {source['title']})"
    context = f"Primary source: {source['title']} ({source.get('authors', '')}, {source.get('year', '')}).\n" \
              f"What to read: {source.get('locator', '')}\nFocus: {source.get('focus', '')}\nAbout: {source.get('summary', '')}"
    return {"source": "primary_source", "dedupe_key": _key("primary_source", day.isoformat(), prompt), "task_id": task_id, "week_start": None,
            "skill_slug": skill_slug, "prompt": prompt[:1000], "context": context[:MAX_CONTEXT], "due_on": day + timedelta(days=2)}
