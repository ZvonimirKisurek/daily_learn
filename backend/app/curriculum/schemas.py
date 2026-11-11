"""Pydantic schemas = the *contract* between the LLM and our database.

WHY STRUCTURED OUTPUT?
An LLM is a next-token predictor; left alone it returns prose. We need data a program can store and a
UI can render, so we demand JSON that matches a schema. Two independent layers enforce it:

  1. Constrained decoding (server side). The JSON Schema is sent as `response_format` (strict json_schema). The API compiles it
     into a grammar and, at every decoding step, masks tokens that would break it. The model *cannot* emit
     malformed JSON or a missing field. This guarantees SHAPE only.
  2. Pydantic validation (client side, below). The API cannot enforce what a JSON Schema cannot express:
     length limits, ranges, list sizes, and - most importantly - *grounding*: "this article id must be one
     of the 50 we showed you". Failures here trigger a repair re-prompt (see llm.py).

Why Pydantic rather than hand-written checks? One class gives us (a) the JSON Schema we send to the API,
(b) parsing + type coercion, (c) precise, machine-readable error locations we can feed back to the model
so it can fix exactly what was wrong. Constraints the API grammar does not support (min/max, length) are
stripped from the wire schema by `llm.strict_schema` (they become hints in each field's description) and enforced here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from app.curriculum import content_checks as cc


@dataclass(frozen=True)
class GroundingContext:
    """Closed-world facts passed to `model_validate(..., context=...)`.

    Anything the model references must come from these sets. This is the core anti-hallucination device:
    the model never types a URL or a paper title that we then trust; it can only *point at* ids we gave it.
    """

    article_ids: frozenset[str]  # short ids shown in the prompt, e.g. "A7"
    skill_slugs: frozenset[str]
    required_focus: frozenset[str] | None = None  # set in roadmap mode: focus_skills must equal this
    rubric: "Rubric | None" = None  # set when GRADING: the evaluation must score exactly these criteria
    required_skill: str | None = None  # set for a daily lesson: the learner's current level fixes the skill


def _grounding(info: ValidationInfo) -> GroundingContext | None:
    return info.context if isinstance(info.context, GroundingContext) else None


def _article_id(v: str, info: ValidationInfo) -> str:
    g = _grounding(info)
    if g is not None and v not in g.article_ids:
        raise ValueError(f"{v!r} is not one of the provided article ids (use only ids that appear in <articles>)")
    return v


def _skill_slug(v: str, info: ValidationInfo) -> str:
    g = _grounding(info)
    if g is not None and v not in g.skill_slugs:
        raise ValueError(f"{v!r} is not a known skill slug; allowed: {sorted(g.skill_slugs)}")
    return v


def _no_urls(v: str) -> str:
    # Links come from our database, never from the model. A URL in generated text is, by construction, something the
    # model recalled or invented - reject it and let the repair loop remove it. (localhost addresses are fine: commands use them.)
    if cc.external_urls(v):
        raise ValueError("do not include URLs; refer to sources by article id ([A3]) or by library/function name")
    return v


ArticleRef = Annotated[str, AfterValidator(_article_id)]
SkillSlug = Annotated[str, AfterValidator(_skill_slug)]
Text = Annotated[str, AfterValidator(_no_urls)]


class _Strict(BaseModel):
    # extra="forbid" -> `additionalProperties: false` in the schema, which strict structured outputs require.
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------ weekly theme (a graduate-seminar syllabus)
class DayPlan(_Strict):
    day: int = Field(ge=1, le=7, description="ISO weekday: 1=Monday ... 7=Sunday")
    focus: Text = Field(max_length=140, description="One-line focus for the day, e.g. 'Write a training loop from scratch'")
    skill_slug: SkillSlug
    intensity: Literal["light", "normal", "stretch"] = Field(description="Weekend days should be light")
    reading_hint: Text = Field(max_length=200, description="What the day's chapter should teach")
    build_hint: Text = Field(max_length=200, description="What the day's hands-on project should build")


class WeeklyThemeOutput(_Strict):
    title: Text = Field(max_length=90, description="Seminar title, e.g. 'Retrieval as Representation: Embeddings, Indexes and Evaluation'")
    rationale: Text = Field(max_length=900, description="The week's thesis in one dense paragraph")
    seminar_overview: Text = Field(
        max_length=7000,
        description="The syllabus essay (450-800 words, markdown, continuous prose, no bullet lists): why this topic is "
                    "critical for an ML engineer, the intellectual arc of the week, the research and industry evidence, "
                    "citing supplied articles as [A3]",
    )
    core_questions: list[Text] = Field(min_length=3, max_length=5, description="The research-style questions the week should let you answer")
    market_signals: list[Text] = Field(min_length=2, max_length=5, description="Concrete trends seen in the supplied articles")
    focus_skills: list[SkillSlug] = Field(min_length=1, max_length=3)
    learning_objectives: list[Text] = Field(min_length=3, max_length=5, description="Observable outcomes by Sunday, starting with a verb (derive, implement, evaluate...)")
    key_article_ids: list[ArticleRef] = Field(min_length=3, max_length=10, description="Articles that best evidence the theme")
    daily_plan: list[DayPlan] = Field(min_length=7, max_length=7, description="Exactly one entry per weekday 1..7")

    @model_validator(mode="after")
    def _syllabus_quality(self, info: ValidationInfo) -> WeeklyThemeOutput:
        g = _grounding(info)
        if g is not None and g.required_focus is not None and set(self.focus_skills) != set(g.required_focus):
            raise ValueError(f"focus_skills must be exactly {sorted(g.required_focus)} (the roadmap's skills for this week)")
        if sorted(d.day for d in self.daily_plan) != list(range(1, 8)):
            raise ValueError("daily_plan must contain each day 1..7 exactly once")
        problems = cc.check_prose(self.seminar_overview, min_words=400, max_words=900,
                                  article_ids=g.article_ids if g else None, min_citations=3 if g and len(g.article_ids) >= 3 else 0)
        if problems:
            raise ValueError("seminar_overview: " + "; ".join(problems))
        return self


# ------------------------------------------------------------------------------ rubric + hands-on
class RubricCriterion(_Strict):
    name: Text = Field(max_length=60, description="Short unique name, e.g. 'Correct cosine similarity'")
    description: Text = Field(max_length=300, description="Observable evidence in the submission that earns full points")
    max_points: int = Field(ge=1, le=10)
    must_pass: bool = Field(description="If true, earning less than half of this criterion's points fails the whole task")


class Rubric(_Strict):
    pass_score: int = Field(ge=5, le=9, description="Minimum overall score (1-10) to pass")
    criteria: list[RubricCriterion] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def _well_formed(self) -> Rubric:
        names = [c.name.strip().lower() for c in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError("rubric criterion names must be unique")
        if sum(c.max_points for c in self.criteria) < 6:
            raise ValueError("rubric max_points must sum to at least 6")
        return self


def compute_grade(rubric: Rubric, results: list["CriterionResult"]) -> tuple[int, bool]:
    """The ONLY definition of the grade. score = round(10 * earned / possible), floor 1; passed = score >= pass_score
    AND every must_pass criterion earned at least half of its points. The grader model is not trusted to do this arithmetic."""
    by_name = {r.criterion.strip().lower(): r for r in results}
    total = sum(c.max_points for c in rubric.criteria)
    earned = sum(min(by_name[c.name.strip().lower()].points, c.max_points) for c in rubric.criteria)
    score = max(1, round(10 * earned / total))
    must_ok = all(by_name[c.name.strip().lower()].points >= math.ceil(c.max_points / 2) for c in rubric.criteria if c.must_pass)
    return score, score >= rubric.pass_score and must_ok


class HandsOnStep(_Strict):
    title: Text = Field(max_length=100)
    instructions: Text = Field(max_length=1000, description="Imperative and specific: name the exact file to create or edit and what goes in it")
    commands: list[Text] = Field(max_length=6, description="Exact terminal commands for this step, copy-pasteable; empty list if none")
    expected_output: Text = Field(max_length=600, description="Exactly what the learner should see after this step (output, file, plot); empty string if none")


class HandsOnTask(_Strict):
    title: Text = Field(max_length=100)
    skill_slug: SkillSlug
    difficulty: int = Field(ge=1, le=3)
    objective: Text = Field(max_length=500)
    prerequisites: list[Text] = Field(max_length=5, description="Things to install or know first; may be empty")
    project_layout: list[Text] = Field(min_length=1, max_length=8, description="Every file the project will contain, e.g. 'lab/embed.py'")
    steps: list[HandsOnStep] = Field(min_length=2, max_length=10, description="Step 1, Step 2, ... in order (2-5 for a 5-10 minute exercise)")
    deliverable: Text = Field(max_length=300, description="A checkable artifact: a script, a passing test, a printed table")
    submission_instructions: Text = Field(max_length=500, description="Exactly what to paste for grading (the code AND the terminal output of running it)")
    docs_to_consult: list[Text] = Field(max_length=4, description="Official docs by library + symbol name, no URLs; may be empty")
    estimated_minutes: int = Field(ge=5, le=90, description="5-10 for a daily lesson")
    stretch_goal: Text | None = Field(description="Optional extension, or null")
    rubric: Rubric


# ------------------------------------------------------------------------------ multiple choice (reading quiz, level exam)
class QuizQuestion(_Strict):
    question: Text = Field(max_length=300)
    options: list[Text] = Field(min_length=4, max_length=4, description="Exactly 4 options, one correct; distractors are plausible misconceptions")
    answer: int = Field(ge=0, le=3, description="Index (0-3) of the correct option")
    explanation: Text = Field(max_length=400, description="Why the correct option is right (and the tempting wrong one is wrong)")

    @model_validator(mode="after")
    def _distinct(self) -> QuizQuestion:
        if len({o.strip().lower() for o in self.options}) != len(self.options):
            raise ValueError("the 4 options must all be different")
        return self


def _unique_questions(questions: list[QuizQuestion]) -> None:
    if len({q.question.strip().lower() for q in questions}) != len(questions):
        raise ValueError("questions must all be different")


class LevelExamOutput(_Strict):
    """A level-completion exam. The model writes it; code shuffles the options and grades it (levels.grade_choices)."""
    questions: list[QuizQuestion] = Field(min_length=8, max_length=12)

    @model_validator(mode="after")
    def _ok(self) -> LevelExamOutput:
        _unique_questions(self.questions)
        return self


# ------------------------------------------------------------------------------ daily plan (stage 1 of the chain)
class ChapterSection(_Strict):
    heading: Text = Field(max_length=80)
    key_points: Text = Field(max_length=900, description="What this section must establish: the idea, the analogy, the example")
    target_words: int = Field(ge=80, le=350)


class ReadingPlan(_Strict):
    chapter_title: Text = Field(max_length=100)
    skill_slug: SkillSlug
    difficulty: int = Field(ge=1, le=3, description="1=from scratch, 3=research-level")
    learning_goals: list[Text] = Field(min_length=2, max_length=4, description="After reading you can... (observable)")
    sections: list[ChapterSection] = Field(min_length=3, max_length=6, description="Ordered outline of a 5-10 minute read; no quiz section (the quiz is separate)")
    inspiration_article_ids: list[ArticleRef] = Field(min_length=1, max_length=3, description="Supplied articles to use as real-world examples")
    estimated_minutes: int = Field(ge=5, le=10)
    quiz: list[QuizQuestion] = Field(min_length=4, max_length=5, description="Checks understanding of THIS reading; must be answerable from it")

    @model_validator(mode="after")
    def _budget(self) -> ReadingPlan:
        total = sum(s.target_words for s in self.sections)
        if not 600 <= total <= 1100:
            raise ValueError(f"section target_words must sum to 600-1100 (a 5-10 minute read), got {total}")
        _unique_questions(self.quiz)
        return self


class InsightPlan(_Strict):
    headline: Text = Field(max_length=120)
    thesis: Text = Field(max_length=400, description="The one claim the essay will argue")
    skill_slug: SkillSlug
    evidence_article_ids: list[ArticleRef] = Field(min_length=1, max_length=4)


class DailyPlanOutput(_Strict):
    reading: ReadingPlan
    hands_on_task: HandsOnTask
    market_insight: InsightPlan

    @model_validator(mode="after")
    def _time_budget(self, info: ValidationInfo) -> DailyPlanOutput:
        h = self.hands_on_task
        if not 5 <= h.estimated_minutes <= 10:
            raise ValueError(f"hands_on_task.estimated_minutes must be 5-10 (a short daily exercise), got {h.estimated_minutes}")
        if len(h.steps) > 5:
            raise ValueError(f"hands_on_task has {len(h.steps)} steps; a 5-10 minute exercise has 2-5")
        if not 2 <= len(h.rubric.criteria) <= 4:
            raise ValueError("hands_on_task.rubric must have 2-4 criteria")
        g = _grounding(info)
        if g is not None and g.required_skill and {self.reading.skill_slug, h.skill_slug} != {g.required_skill}:
            raise ValueError(f"reading.skill_slug and hands_on_task.skill_slug must both be {g.required_skill!r} (the learner's current level)")
        return self


# ------------------------------------------------------------------------------ evaluation (grading a submission)
class CriterionResult(_Strict):
    criterion: Text = Field(max_length=60, description="Exactly the rubric criterion's name")
    points: int = Field(ge=0, le=10)
    max_points: int = Field(ge=1, le=10, description="The rubric's max_points for this criterion")
    evidence: Text = Field(max_length=500, description="Point at the specific part of the submission (a line, a function, an output) that justifies the points")


class TaskEvaluationSchema(_Strict):
    """What the grader must return. `score` and `passed` are checked against the rubric arithmetic (compute_grade),
    so a grader that is confused, flattered or prompt-injected by the submission cannot award a pass its own scores do not support."""

    criteria: list[CriterionResult]
    score: int = Field(ge=1, le=10)
    passed: bool
    summary: Text = Field(max_length=600, description="2-3 sentence verdict")
    strengths: list[Text] = Field(min_length=1, max_length=5)
    problems: list[Text] = Field(max_length=6, description="Specific defects, each tied to the submission; empty if flawless")
    how_to_improve: list[Text] = Field(min_length=1, max_length=6, description="Concrete next actions, most important first")
    feedback_markdown: Text = Field(max_length=5000, description="Detailed professor-style feedback in markdown: what you did well, what is wrong and why, how to fix it")

    @model_validator(mode="after")
    def _consistent_with_rubric(self, info: ValidationInfo) -> TaskEvaluationSchema:
        g = _grounding(info)
        if g is None or g.rubric is None:
            return self
        rubric = g.rubric
        want = {c.name.strip().lower(): c for c in rubric.criteria}
        got = [r.criterion.strip().lower() for r in self.criteria]
        if sorted(got) != sorted(want):
            raise ValueError(f"criteria must be exactly the rubric's, each once: {[c.name for c in rubric.criteria]}")
        for r in self.criteria:
            c = want[r.criterion.strip().lower()]
            if r.max_points != c.max_points:
                raise ValueError(f"max_points for {c.name!r} must be {c.max_points}")
            if r.points > c.max_points:
                raise ValueError(f"points for {c.name!r} cannot exceed {c.max_points}")
        score, passed = compute_grade(rubric, self.criteria)
        if self.score != score or self.passed != passed:
            raise ValueError(f"score and passed must follow the rubric arithmetic: the criteria points give score={score}, "
                             f"passed={str(passed).lower()} (pass_score={rubric.pass_score}); fix them and make the feedback consistent")
        return self


# ------------------------------------------------------------------------------ article summaries
class ArticleSummary(_Strict):
    id: ArticleRef
    summary: Text = Field(max_length=360, description="1-2 neutral sentences using only the supplied title and excerpt")


class SummaryBatch(_Strict):
    summaries: list[ArticleSummary]

    @model_validator(mode="after")
    def _covers_every_article(self, info: ValidationInfo) -> SummaryBatch:
        g = _grounding(info)
        ids = [s.id for s in self.summaries]
        if len(ids) != len(set(ids)) or (g is not None and set(ids) != set(g.article_ids)):
            raise ValueError("return exactly one summary for every supplied article id, each once")
        return self
