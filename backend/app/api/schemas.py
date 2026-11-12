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
    quiz_questions: int | None = Field(default=None, description="number of quiz questions (null on lessons made before quizzes)")


class InsightOut(BaseModel):
    headline: str
    thesis: str
    skill_slug: str
    body: str = Field(description="the ~500-word analytical essay, markdown")
    word_count: int
    evidence: list[ArticleRefOut]


class LessonInfo(BaseModel):
    track: str = "ai"
    canon: list[str] = Field(default_factory=list, description="the level's university reading list (courses, books, papers)")
    level: int
    of: int
    tier: int
    tier_name: str
    title: str
    topic: str
    review: bool
    lesson_number: int
    lessons_required: int


class BriefingPayloadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesson: LessonInfo | None = Field(default=None, description="the learning-path level/topic this lesson taught (null: legacy briefing)")
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
    has_quiz: bool = False
    quiz_attempts: int = 0
    quiz_score: int | None = None
    xp_awarded: int | None = None


class BriefingOut(BaseModel):
    id: UUID
    day: date
    completed_at: datetime | None
    level: int | None = Field(default=None, description="learning-path level; null = a legacy briefing whose tasks are not gated")
    track: str = "ai"
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
    track: str = Field(default="ai", description="which learning track (and so which skill graph) the skill belongs to")
    level: float
    target: float
    percent_to_target: float = Field(description="level / target * 100, capped at 100")
    delta_7d: float = Field(description="points gained in the last 7 days")
    history: list[HistoryPoint]


class TodayOut(BaseModel):
    date: date
    track: str = "ai"
    state: Literal["ready", "pending", "rest"] = Field(description="'pending' = no lesson generated yet for today; 'rest' = a weekend, no lesson is made")
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
    xp: int = Field(default=0, description="XP earned by this completion (incl. the lesson bonus)")


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


class FeedLevel(BaseModel):
    index: int
    title: str
    tier_name: str
    skill: str


class FeedPage(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None
    for_level: FeedLevel | None = Field(default=None, description="sort=for_you: the level the ranking was matched to")


class QueueOut(BaseModel):
    queued: bool
    reason: str | None = None
    run_id: UUID | None = None  # Bitcoin runs: poll /api/bitcoin/latest and watch `run` for this id


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
    quiz: list["PublicQuestion"] | None = Field(default=None, description="the reading quiz, WITHOUT answers")
    lesson: LessonInfo | None = None
    locked: str | None = Field(default=None, description="why this task cannot be done yet (lesson order), or null")
    track: str = "ai"


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
    xp: int = 0


# ----------------------------------------------------------------------------------------------- learning path
class PublicQuestion(BaseModel):
    question: str
    options: list[str]
    tier: int | None = None


class AnswersIn(BaseModel):
    answers: list[int] = Field(max_length=40, description="the chosen option index for each question, in order")


class ChoiceResult(BaseModel):
    correct: bool
    chosen: int
    answer: int | None = Field(default=None, description="the right option; withheld on a failed reading quiz")
    explanation: str | None = None


class QuizOut(BaseModel):
    passed: bool
    correct: int
    total: int
    pass_mark: int
    results: list[ChoiceResult]
    attempts: int
    completion: CompletionOut | None


class PathLevel(BaseModel):
    index: int
    tier: int
    tier_name: str
    skill: str
    title: str
    summary: str
    topics: list[str]
    status: Literal["completed", "current", "locked"]
    via: Literal["exam", "placement"] | None
    score: int | None
    total: int | None


class CurrentLevel(BaseModel):
    index: int
    tier: int
    tier_name: str
    skill: str
    title: str
    summary: str
    topics: list[str]
    lessons_required: int


class LastExam(BaseModel):
    correct: int
    total: int
    passed: bool | None
    submitted_at: datetime


class ExamStatus(BaseModel):
    unlocked: bool
    attempts_today: int
    attempts_max: int
    pass_percent: int
    questions: int
    last: LastExam | None
    open_exam_id: UUID | None


class PlacementStatus(BaseModel):
    taken: bool
    available_today: bool


class ProgressOut(BaseModel):
    track: str = "ai"
    track_label: str = ""
    level: CurrentLevel
    levels_total: int
    lessons_done: int = Field(description="lessons fully finished on the current level")
    graduated: bool
    exam: ExamStatus
    xp_total: int
    xp_today: int
    streak_days: int
    knowledge_score: int = Field(description="0-100: share of the path proven by exams or placement")
    placement: PlacementStatus
    path: list[PathLevel]


class ExamStartIn(BaseModel):
    kind: Literal["placement", "level"]
    track: Literal["ai", "management"] = "ai"


class ExamOut(BaseModel):
    id: UUID
    kind: Literal["placement", "level"]
    track: str = "ai"
    level: int | None
    level_title: str | None
    questions: list[PublicQuestion]
    total: int
    pass_mark: int | None


class ExamQuestionResult(ChoiceResult):
    question: str
    options: list[str]


class PlacementTier(BaseModel):
    tier: int
    name: str
    correct: int
    total: int


class PlacementOut(BaseModel):
    known_tier: int
    start_level: int
    tiers: list[PlacementTier]


class ExamResultOut(BaseModel):
    id: UUID
    kind: Literal["placement", "level"]
    track: str = "ai"
    level: int | None
    correct: int
    total: int
    passed: bool | None = Field(description="level exams only")
    pass_mark: int | None
    results: list[ExamQuestionResult]
    previous_level: int
    new_level: int
    new_level_title: str
    xp: int
    placement: PlacementOut | None


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


class BitcoinRunOut(BaseModel):
    id: UUID
    trigger: str
    status: Literal["queued", "running", "succeeded", "failed"]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stalled: bool = Field(description="queued for over 2 minutes: nothing is consuming the task queue (is the Celery worker running?)")


class BitcoinLatestOut(BaseModel):
    run: BitcoinRunOut | None = Field(default=None, description="the most recent forecast run (any status)")
    price: BitcoinPriceOut | None
    prediction: BitcoinPredictionOut | None
    news: list[BitcoinNewsOut]
    disclaimer: str = BITCOIN_DISCLAIMER


class BitcoinPulseOut(BaseModel):
    price_updated: bool
    news_added: int
    forecast: Literal["current", "started", "running", "cooldown", "busy", "error"]
    reason: str | None
    run_id: UUID | None


# ----------------------------------------------------------------------------------------------- Bitcoin bet advisor
class BetIn(BaseModel):
    balance: float = Field(ge=0, le=1e9, description="total balance on the prediction platform right now (USD)")
    strike: float | None = Field(default=None, gt=0, le=1e8, description="the price in the question: 'BTC above $strike at settlement'; default: the current price (plain up/down)")
    hours_to_settle: float | None = Field(default=None, gt=0, le=72, description="default: until the next daily settlement")
    reset: bool = Field(default=False, description="start a fresh bankroll (after a deposit or withdrawal): vault 0, peak = balance")


class BetCheckIn(BaseModel):
    sell_price: float = Field(gt=0, lt=1, description="what the market pays right now for one share you hold (0-1)")
    paid_price: float | None = Field(default=None, gt=0, lt=1, description="what you paid per share; default: the recommendation's highest acceptable price")


class BetAdviceOut(BaseModel):
    """The recommendation plus every number behind it (see bitcoin/betting.py). Deterministic arithmetic, not LLM text."""
    model_config = ConfigDict(extra="allow")
    id: UUID
    created_at: datetime
    action: Literal["BUY_YES", "BUY_NO", "HOLD_CASH", "HALT"]
    reason: str
    shares: int
    cost: float
    entry_price: float | None
    prob_win: float | None
    edge_yes: float | None = None  # only on check-ins made before the share price stopped being an input
    edge_no: float | None = None
    kelly_full: float | None
    fraction: float
    take_profit: float | None
    stop_loss: float | None
    max_profit: float
    expected_profit: float
    inputs: dict
    market: dict
    model: dict
    bankroll: dict
    warnings: list[str]


class BetCheckOut(BaseModel):
    action: Literal["SELL", "HOLD"]
    rule: Literal["exit", "take_profit", "stop_loss", "hold"]
    reason: str
    side: Literal["BUY_YES", "BUY_NO"]
    sell_price: float
    entry_price: float
    prob_win_now: float
    unrealized: float
    shares: int
    spot: float
    strike: float
    hours_left: float


class BetStateOut(BaseModel):
    balance: float
    active: float
    vault: float
    peak: float
    updated_at: datetime


class BetHistoryRow(BaseModel):
    id: UUID
    created_at: datetime
    balance: float
    vault: float
    pnl: float | None
    action: str
    shares: int
    entry_price: float | None
    cost: float
    strike: float
    is_reset: bool


class BetLiveOut(BaseModel):
    """The last check-in re-evaluated at the current price."""
    spot: float
    p_yes: float
    action: Literal["BUY_YES", "BUY_NO", "HOLD_CASH", "HALT"]
    changed: bool = Field(description="the rules would now say something different from the stored recommendation")
    hours_left: float


class BetOverviewOut(BaseModel):
    live: BetLiveOut | None = None
    state: BetStateOut | None
    last: BetAdviceOut | None
    open_position: bool
    history: list[BetHistoryRow]
    defaults: dict
    params: dict
    disclaimer: str = BITCOIN_DISCLAIMER


TaskDetailOut.model_rebuild()  # it references PublicQuestion, declared further down
