"""The canon of primary sources (backend/curriculum/canon.yaml): human-written references the planner may only point at.

Every generated chapter is a secondary source: an LLM's summary of the field. Learners who reach the top of a field read
the primary sources (the paper, the textbook chapter, the course) and can say where an idea came from. The daily plan
therefore assigns ONE primary source next to the chapter, chosen by the model from a closed list filtered to the day's
skill. The model writes only `source_id` + what to focus on + a guiding question; title, authors, locator and link come
from this file, so no reference can be invented.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

CANON_FILE = Path(__file__).parents[2] / "curriculum" / "canon.yaml"


class CanonSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: Literal["paper", "book", "course", "video", "blog", "doc"]
    title: str
    authors: str
    year: int
    locator: str
    url: str | None
    skills: list[str] = Field(min_length=1)
    level: int = Field(ge=1, le=3)
    minutes: int = Field(ge=5, le=300)
    summary: str


class Canon(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[CanonSource]


@lru_cache
def load_canon(path: str | None = None) -> dict[str, CanonSource]:
    data = Canon.model_validate(yaml.safe_load(Path(path or CANON_FILE).read_text()))
    ids = [s.id for s in data.sources]
    if len(ids) != len(set(ids)):
        raise ValueError("canon.yaml has duplicate ids")
    return {s.id: s for s in data.sources}


def for_skill(skill: str, *, difficulty: int = 2, limit: int = 12, exclude: set[str] | None = None) -> list[CanonSource]:
    """Candidates for today: tagged with the skill, not assigned recently, nearest in level to today's difficulty first."""
    exclude = exclude or set()
    pool = [s for s in load_canon().values() if skill in s.skills]
    fresh = [s for s in pool if s.id not in exclude] or pool  # once every source has been used, repetition is fine
    fresh.sort(key=lambda s: (abs(s.level - difficulty), s.minutes, s.id))
    return fresh[:limit]
