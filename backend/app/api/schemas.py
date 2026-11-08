"""API response models.

Pydantic plays two roles in this app, with the same machinery:
  * LLM boundary (curriculum/schemas.py): an untrusted, probabilistic producer must satisfy a contract.
  * HTTP boundary (this file): FastAPI uses `response_model` to (1) serialise, (2) DROP any field not declared
    (nothing leaks accidentally, e.g. raw embeddings), (3) validate what we send, and (4) generate the
    OpenAPI schema the frontend's TypeScript types mirror.
The briefing models below *inherit* the LLM output models, so the shape the model is asked to produce and the
shape the UI receives cannot drift apart - one source of truth, extended with the fields our code added
(real URLs resolved from article ids).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.curriculum.schemas import HandsOnTask, TaskEvaluationSchema, WeeklyThemeOutput
from app.ingestion.models import Category


class ArticleRefOut(BaseModel):
    article_uuid: str
    ref_id: str | None = Field(default=None, description="the [A7] id used in the text's citations")
    title: str
    url: str
    source: str
    category: str


class ReadingOut(BaseModel):
    chapter_title: str
    skill_slug: str
    difficulty: int
    learning_goals: list[str]
    estimated_minutes: int
    word_count: int
    section_headings: list[str]
    inspiration: list[ArticleRefOut]


class InsightOut(BaseModel):
    headline: str
    thesis: str
    skill_slug: str
    body: str = Field(description="the ~500-word analytical essay, markdown")
    word_count: int
    evidence: list[ArticleRefOut]


class BriefingPayloadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    morning_reading: ReadingOut
    hands_on_task: HandsOnTask
    market_insight: InsightOut


class TaskOut(BaseModel):
    id: UUID
    kind: Literal["reading", "hands_on", "insight"]
    skill_slug: str
    difficulty: int
    title: str
    completed: bool
    completed_at: datetime | None
    points_awarded: float | None
    attempt_count: int = 0
    evaluation_score: int | None = None
    evaluation_passed: bool | None = None
    has_content: bool = False


class BriefingOut(BaseModel):
    id: UUID
    day: date
    completed_at: datetime | None
    payload: BriefingPayloadOut
    tasks: list[TaskOut]


class RoadmapInfo(BaseModel):
    index: int
    of: int
    title: str
    goal: str
    deliverable: str
    milestones: list[str]


class WeeklyThemeBody(WeeklyThemeOutput):
    key_articles: list[ArticleRefOut]
    roadmap: RoadmapInfo | None = None


class WeeklyThemeOut(BaseModel):
    id: UUID
    week_start: date
    theme: WeeklyThemeBody


class HistoryPoint(BaseModel):
    at: datetime
    level: float


class SkillOut(BaseModel):
    slug: str
    name: str
    level: float
    target: float
    percent_to_target: float = Field(description="level / target * 100, capped at 100")
    delta_7d: float = Field(description="points gained in the last 7 days")
    history: list[HistoryPoint]


class TodayOut(BaseModel):
    date: date
    state: Literal["ready", "pending"] = Field(description="'pending' = no briefing generated yet for today")
    briefing: BriefingOut | None
    weekly_theme: WeeklyThemeOut | None
    skills: list[SkillOut]


class SkillDelta(BaseModel):
    slug: str
    level: float
    delta: float


class CompletionOut(BaseModel):
    task: TaskOut
    skill: SkillDelta
    briefing_completed: bool
    already_completed: bool


class SkillMatch(BaseModel):
    skill_slug: str
    skill_name: str
    similarity: float = Field(description="cosine similarity between the article and the skill description, -1..1")
    strength: Literal["strong", "moderate", "weak"]


class FeedItem(BaseModel):
    id: UUID
    title: str
    url: str
    source_slug: str
    source_name: str
    category: Category
    published_at: datetime
    summary: str | None = Field(description="the feed's own excerpt")
    ai_summary: str | None = Field(description="LLM rewrite of title+excerpt; null until the summariser has run")
    engagement: int | None
    skill_match: SkillMatch | None = Field(description="best-matching skill; null if embeddings are missing")
    relevance: float = Field(description="similarity weighted by how big that skill's gap is; used by sort=relevance")
    also_covered_by: list[str] = Field(description="other sources whose near-identical story was collapsed into this one")


class FeedPage(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None


class QueueOut(BaseModel):
    queued: bool
    reason: str | None = None


class ReadingDetail(ReadingOut):
    content: str | None = Field(description="the full chapter, markdown with LaTeX and fenced code")


class TaskDetailOut(BaseModel):
    task: TaskOut
    day: date
    reading: ReadingDetail | None
    hands_on: HandsOnTask | None
    insight: InsightOut | None
    submission: str | None = Field(description="the learner's latest submission")
    evaluation: TaskEvaluationSchema | None = Field(description="the latest graded result")
    attempts_used: int
    attempts_max: int
    notes: str = Field(default="", description="the learner's notebook for this task")
    archived: bool = Field(default=False, description="the task belongs to a past day (in the learner's timezone)")


class NotesIn(BaseModel):
    notes: str = Field(max_length=50000)


class NotesOut(BaseModel):
    notes: str
    updated_at: datetime | None


class SubmissionIn(BaseModel):
    submission: str = Field(min_length=20, max_length=20000, description="your code AND the terminal output of running it")


class SubmissionOut(BaseModel):
    task: TaskOut
    evaluation: TaskEvaluationSchema
    passed: bool
    skill: SkillDelta | None = Field(description="set only when the submission passed and moved a skill")
    briefing_completed: bool
    attempts_used: int
    attempts_max: int


# ----------------------------------------------------------------------------------------------- Phase 11: Bitcoin
BITCOIN_DISCLAIMER = ("AI-generated, experimental forecast for learning purposes. Not financial advice. Crypto prices are highly "
                      "volatile and short-horizon forecasts are frequently wrong.")


class BitcoinPriceOut(BaseModel):
    price_usd: float
    source: str
    fetched_at: datetime


class BitcoinNewsOut(BaseModel):
    id: UUID
    source_name: str
    title: str
    url: str
    summary: str
    btc_relevant: bool
    published_at: datetime


class BitcoinHorizonOut(BaseModel):
    key: Literal["1d", "3d", "7d", "14d"]
    label: str
    days: int
    target_date: date
    predicted_price: float
    change_pct: float = Field(description="forecast vs the price at prediction time, in percent")
    confidence: int = Field(description="probability (0-100) that the forecast DIRECTION is right; 50 = coin flip")
    reasoning: str = Field(description="cites headlines as [N#] (see cited_news) and indicators as [T]")
    one_sigma_usd: float | None = Field(default=None, description="typical random-walk move over this horizon, for scale")


class BitcoinCitedNewsOut(BaseModel):
    ref: str
    title: str
    url: str
    source: str


class BitcoinHistoryPointOut(BaseModel):
    date: date
    close: float


class BitcoinPredictionOut(BaseModel):
    id: UUID
    created_at: datetime
    current_price: float
    model: str
    trigger: str
    sentiment_score: int
    sentiment_summary: str
    technical_summary: str
    horizons: list[BitcoinHorizonOut]
    technicals: dict = Field(description="exactly the indicator values the model was given")
    history: list[BitcoinHistoryPointOut]
    cited_news: list[BitcoinCitedNewsOut]


class BitcoinLatestOut(BaseModel):
    price: BitcoinPriceOut | None
    prediction: BitcoinPredictionOut | None
    news: list[BitcoinNewsOut]
    disclaimer: str = BITCOIN_DISCLAIMER
