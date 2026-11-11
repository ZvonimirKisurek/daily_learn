"""The learning path: levels, XP, quizzes and exams. Deterministic code - the LLM writes questions, it never grades them.

    placement exam (static) ──► start level ──► daily lesson on the level's next topic ──► reading quiz (gate)
                                                    ▲                                        │ pass
                                                    │ same level (review)          hands-on (graded) ──► essay
                                                    │                                        │ all three done = 1 lesson
                                          fail ◄── level exam (unlocks after N lessons) ◄────┘
                                                    │ pass (>= 80%)
                                                    ▼
                                               next level

Why gate at all? Spaced, checked practice beats re-reading: a quiz you must pass forces retrieval (the "testing effect"),
and an exam that can be failed makes "level 5" mean something. The grading here is plain arithmetic over answer keys
stored server-side, so a level cannot be earned by persuading a model.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_DIR = Path(__file__).resolve().parents[2] / "curriculum"
# A TRACK is an independent subject with its own path, skills, sources and daily lesson.
TRACKS = ("ai", "management")
TRACK_LABEL = {"ai": "AI & Machine Learning", "management": "Management & Leadership"}
_FILES = {"ai": ("levels.yaml", "placement.yaml"), "management": ("management_levels.yaml", "management_placement.yaml")}
LEVELS_FILE = _DIR / "levels.yaml"
PLACEMENT_FILE = _DIR / "placement.yaml"

# XP: a motivation signal (Duolingo-style), separate from the skill matrix. Building and passing exams earn the most.
XP_QUIZ = 10
XP_QUIZ_FIRST_TRY = 5
XP_HANDS_ON_BASE = 10
XP_PER_SCORE_POINT = 2  # a 9/10 lab earns 10 + 18
XP_INSIGHT = 5
XP_LESSON_BONUS = 10
XP_EXAM_PASS = 50


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Level(_Strict):
    index: int = Field(ge=1)
    tier: int = Field(ge=1)
    skill: str
    title: str
    summary: str
    topics: list[str] = Field(min_length=2)

    @property
    def lessons_required(self) -> int:
        return len(self.topics)


class Ladder(_Strict):
    pass_percent: int = Field(ge=50, le=100)
    exam_questions: int = Field(ge=5, le=20)
    exam_attempts_per_day: int = Field(ge=1)
    tiers: dict[int, str]
    levels: list[Level]

    @model_validator(mode="after")
    def _ordered(self) -> Ladder:
        if [lv.index for lv in self.levels] != list(range(1, len(self.levels) + 1)):
            raise ValueError("levels must be numbered 1..N in order")
        if any(lv.tier not in self.tiers for lv in self.levels):
            raise ValueError("every level's tier must be declared in tiers")
        return self

    def level(self, index: int) -> Level:
        return self.levels[max(1, min(index, len(self.levels))) - 1]

    def tier_name(self, tier: int) -> str:
        return self.tiers[tier]

    def first_level_of_tier(self, tier: int) -> int:
        return next(lv.index for lv in self.levels if lv.tier == tier)


class PlacementQuestion(_Strict):
    tier: int
    question: str
    options: list[str] = Field(min_length=3, max_length=5)
    answer: int = Field(ge=0)
    explanation: str


class PlacementBank(_Strict):
    tier_pass: int = Field(ge=1)
    questions: list[PlacementQuestion]


def check_track(track: str) -> str:
    if track not in TRACKS:
        raise ValueError(f"unknown track {track!r}; expected one of {TRACKS}")
    return track


@lru_cache
def ladder(track: str = "ai") -> Ladder:
    return Ladder.model_validate(yaml.safe_load((_DIR / _FILES[check_track(track)][0]).read_text()))


@lru_cache
def placement_bank(track: str = "ai") -> PlacementBank:
    return PlacementBank.model_validate(yaml.safe_load((_DIR / _FILES[check_track(track)][1]).read_text()))


# ----------------------------------------------------------------------------------------------- lessons
def lesson_topic(level: Level, lessons_done: int) -> tuple[str, bool]:
    """Today's topic on this level, and whether it is a REVIEW (every topic already has a finished lesson: the learner is
    waiting to pass the exam, so lessons cycle through the topics again, going deeper)."""
    return level.topics[lessons_done % len(level.topics)], lessons_done >= len(level.topics)


# ----------------------------------------------------------------------------------------------- multiple choice
def shuffle_question(q: dict, rng: random.Random) -> dict:
    """Shuffle the options and move the answer index with them. Models (and humans writing banks) put the right answer
    in position A or B far too often; shuffling removes that tell."""
    order = list(range(len(q["options"])))
    rng.shuffle(order)
    return {**q, "options": [q["options"][i] for i in order], "answer": order.index(q["answer"])}


def public_questions(questions: list[dict]) -> list[dict]:
    """What the browser may see: never the answer or the explanation (those come back only after grading)."""
    return [{"question": q["question"], "options": q["options"], **({"tier": q["tier"]} if "tier" in q else {})} for q in questions]


def pass_mark(total: int, pass_percent: int) -> int:
    return math.ceil(total * pass_percent / 100)


def grade_choices(questions: list[dict], answers: list[int], *, reveal: bool) -> tuple[int, list[dict]]:
    """Score multiple-choice answers. With reveal=False (a failed reading quiz) the learner learns WHICH answers were
    wrong but not the right option, so a retry needs the chapter, not trial and error."""
    if len(answers) != len(questions):
        raise ValueError(f"expected {len(questions)} answers, got {len(answers)}")
    correct, results = 0, []
    for q, a in zip(questions, answers):
        ok = a == q["answer"]
        correct += ok
        r = {"correct": ok, "chosen": a}
        if reveal or ok:
            r |= {"answer": q["answer"], "explanation": q["explanation"]}
        results.append(r)
    return correct, results


def place(questions: list[dict], answers: list[int], tier_pass: int, lad: Ladder) -> dict:
    """Walk the tiers easiest first; a tier is KNOWN when >= tier_pass of its questions are right. Start at the first
    level of the first tier not known (a tier passed after a failed one does not count: foundations come first)."""
    per_tier: dict[int, list[int]] = {}
    for q, a in zip(questions, answers):
        c = per_tier.setdefault(q["tier"], [0, 0])
        c[0] += a == q["answer"]
        c[1] += 1
    known = 0
    for tier in sorted(lad.tiers):
        got = per_tier.get(tier, [0, 0])
        if got[1] and got[0] >= min(tier_pass, got[1]):
            known = tier
        else:
            break
    last = lad.levels[-1].index
    start = last if known >= max(lad.tiers) else lad.first_level_of_tier(known + 1)
    return {"known_tier": known, "start_level": start,
            "tiers": [{"tier": t, "name": lad.tier_name(t), "correct": per_tier.get(t, [0, 0])[0], "total": per_tier.get(t, [0, 0])[1]}
                      for t in sorted(lad.tiers)]}


# ----------------------------------------------------------------------------------------------- XP and streaks
def hands_on_xp(score: int) -> int:
    return XP_HANDS_ON_BASE + XP_PER_SCORE_POINT * score


def streak(completed_days: list[date], today: date) -> int:
    """Consecutive days with a finished lesson, ending today (or yesterday: today's lesson may still be in progress)."""
    days = set(completed_days)
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def knowledge_score(levels_completed: int, total_levels: int) -> int:
    """0-100: the share of the path from zero to expert that you have proven (by exam or placement)."""
    return round(100 * levels_completed / max(total_levels, 1))
