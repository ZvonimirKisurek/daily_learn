from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["research", "industry_news", "engineering_blog", "community", "newsletter", "researcher"]
Kind = Literal["rss", "hackernews", "reddit", "github_trending", "hf_daily_papers"]


class SourceSpec(BaseModel):
    """One entry of sources.yaml."""

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    category: Category
    kind: Kind = "rss"
    url: str
    weight: float = Field(default=1.0, ge=0, le=2)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    track: Literal["ai", "management"] = "ai"  # which learning track the source feeds (set per file, see sources.py)


class RawItem(BaseModel):
    """A fetched item, normalised across all source kinds, before dedup/embedding."""

    title: str
    url: str
    summary: str = ""
    author: str | None = None
    published_at: datetime | None = None
    engagement: int | None = None  # HN points, HF upvotes, GH stars today...
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class SourceState:
    """Runtime state loaded from the `sources` table (empty for dry runs)."""

    id: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    consecutive_failures: int = 0
    last_fetched_at: datetime | None = None


@dataclass
class FetchResult:
    items: list[RawItem] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


@dataclass
class SourceOutcome:
    spec: SourceSpec
    state: SourceState
    result: FetchResult | None = None
    error: str | None = None
    error_kind: str | None = None  # network | http | robots | parse | other
    skipped: str | None = None  # reason, e.g. "circuit breaker", "missing credentials"


class SourceSkipped(Exception):
    """Raised by a fetcher that cannot run (e.g. missing credentials). Not counted as a failure."""
