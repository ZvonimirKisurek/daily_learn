"""A fixed multi-week roadmap (backend/curriculum/roadmap.yaml) that steers the weekly generator and the math drill.

Without a roadmap the weekly theme is *emergent*: the LLM weighs your skill gaps against this week's news.
With one, the roadmap is the SPINE (what to learn, in what order) and the news only chooses timely readings and
current tooling. Why bother? An LLM planning week by week optimises locally; it will happily chase this week's
hype. A curriculum is a global plan: foundations before applications (retrieval -> training -> deployment -> LLM
internals), each week's deliverable feeding the next.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings


class RoadmapDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=1, le=7)
    skill: str
    focus: str


class RoadmapWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    title: str
    focus_skills: list[str]
    goal: str
    deliverable: str
    milestones: list[str]
    daily_outline: list[RoadmapDay] = Field(min_length=7, max_length=7)
    math_topics: list[str] = Field(default_factory=list, description="keys of mastery.mathdrill.GENERATORS drilled this week")


class Quarter(BaseModel):
    """A block of weeks that ends in something PUBLIC: a write-up, a reproduced paper, an open-source contribution.
    Being good is not enough to be recognised as good; the public artifact is the evidence others can check."""
    model_config = ConfigDict(extra="forbid")
    index: int
    title: str
    weeks: list[int] = Field(min_length=2, max_length=2, description="[first, last] week index, inclusive")
    public_deliverable: str
    success_criteria: list[str] = Field(min_length=1)


class Roadmap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    weeks: list[RoadmapWeek]
    quarters: list[Quarter] = Field(default_factory=list)
    beyond: list[str] = Field(default_factory=list, description="what to do after the last scheduled week")


def quarter_for(roadmap: Roadmap, week_index: int) -> Quarter | None:
    return next((q for q in roadmap.quarters if q.weeks[0] <= week_index <= q.weeks[1]), None)


def load_roadmap(path: str | Path) -> Roadmap:
    return Roadmap.model_validate(yaml.safe_load(Path(path).read_text()))


def week_for(roadmap: Roadmap, start: date, week_start: date) -> RoadmapWeek | None:
    """Which roadmap week (1-based) a Monday falls in, or None before the start / after the last week."""
    monday = start - timedelta(days=start.weekday())
    idx = (week_start - monday).days // 7 + 1
    return next((w for w in roadmap.weeks if w.index == idx), None) if week_start >= monday else None


def active_week(settings: Settings, week_start: date) -> tuple[Roadmap, RoadmapWeek] | None:
    if not settings.roadmap_start:
        return None
    rm = load_roadmap(settings.roadmap_file)
    wk = week_for(rm, date.fromisoformat(settings.roadmap_start), week_start)
    return (rm, wk) if wk else None
