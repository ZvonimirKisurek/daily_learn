/** Mirrors backend/app/api/schemas.py. */
import type { Track } from "./tracks";

export type TaskKind = "reading" | "hands_on" | "insight";
export type Category = "research" | "industry_news" | "engineering_blog" | "community" | "newsletter" | "researcher";

export interface ArticleRef { article_uuid: string; ref_id?: string | null; title: string; url: string; source: string; category: string }

export interface Task {
  id: string; kind: TaskKind; skill_slug: string; difficulty: number; title: string;
  completed: boolean; completed_at: string | null; points_awarded: number | null;
  attempt_count: number; evaluation_score: number | null; evaluation_passed: boolean | null; has_content: boolean;
  has_quiz: boolean; quiz_attempts: number; quiz_score: number | null; xp_awarded: number | null;
}

export interface RubricCriterion { name: string; description: string; max_points: number; must_pass: boolean }
export interface Rubric { pass_score: number; criteria: RubricCriterion[] }
export interface HandsOnStep { title: string; instructions: string; commands: string[]; expected_output: string }

export interface HandsOnSpec {
  title: string; skill_slug: string; difficulty: number; objective: string; prerequisites: string[]; project_layout: string[];
  steps: HandsOnStep[]; deliverable: string; submission_instructions: string; docs_to_consult: string[];
  estimated_minutes: number; stretch_goal: string | null; rubric: Rubric;
}

export interface ReadingMeta {
  chapter_title: string; skill_slug: string; difficulty: number; learning_goals: string[]; estimated_minutes: number;
  word_count: number; section_headings: string[]; inspiration: ArticleRef[]; quiz_questions: number | null;
}
export interface InsightMeta {
  headline: string; thesis: string; skill_slug: string; body: string; word_count: number; evidence: ArticleRef[];
}

/** Which learning-path level/topic a lesson taught. Null on briefings made before the learning path (not gated). */
export interface LessonInfo {
  track?: Track; level: number; of: number; tier: number; tier_name: string; title: string; topic: string; review: boolean;
  lesson_number: number; lessons_required: number;
  /** The level's university reading list (courses, books, papers). Absent on lessons made before it existed. */
  canon?: string[];
}

export interface Briefing {
  id: string; day: string; completed_at: string | null; tasks: Task[]; level: number | null; track: Track;
  payload: { lesson: LessonInfo | null; morning_reading: ReadingMeta; hands_on_task: HandsOnSpec; market_insight: InsightMeta };
}

export interface DayPlan {
  day: number; focus: string; skill_slug: string; intensity: "light" | "normal" | "stretch";
  reading_hint: string; build_hint: string;
}
export interface RoadmapInfo { index: number; of: number; title: string; goal: string; deliverable: string; milestones: string[] }

export interface WeeklyTheme {
  id: string; week_start: string;
  theme: {
    title: string; rationale: string; seminar_overview: string; core_questions: string[]; market_signals: string[]; focus_skills: string[];
    learning_objectives: string[]; key_articles: ArticleRef[]; daily_plan: DayPlan[]; roadmap: RoadmapInfo | null;
  };
}

export interface Skill {
  slug: string; name: string; track: Track; level: number; target: number; percent_to_target: number; delta_7d: number;
  history: { at: string; level: number }[];
}

export interface Today { date: string; track: Track; state: "ready" | "pending" | "rest"; briefing: Briefing | null; weekly_theme: WeeklyTheme | null; skills: Skill[] }

export interface Completion {
  task: Task; skill: { slug: string; level: number; delta: number }; briefing_completed: boolean; already_completed: boolean; xp: number;
}

export interface CriterionResult { criterion: string; points: number; max_points: number; evidence: string }
export interface Evaluation {
  criteria: CriterionResult[]; score: number; passed: boolean; summary: string; strengths: string[]; problems: string[];
  how_to_improve: string[]; feedback_markdown: string;
}
export interface SubmissionResult {
  task: Task; evaluation: Evaluation; passed: boolean; skill: { slug: string; level: number; delta: number } | null;
  briefing_completed: boolean; attempts_used: number; attempts_max: number; xp: number;
}

export interface TaskDetail {
  task: Task; day: string; attempts_used: number; attempts_max: number;
  reading: (ReadingMeta & { content: string | null }) | null;
  hands_on: HandsOnSpec | null; insight: InsightMeta | null;
  submission: string | null; evaluation: Evaluation | null;
  /** True when the task belongs to a past day: it stays readable but can no longer be submitted. */
  archived: boolean;
  /** The learner's notebook text for this task ("" if none). */
  notes: string;
  /** The reading quiz, without answers (reading tasks on the learning path). */
  quiz: PublicQuestion[] | null;
  lesson: LessonInfo | null;
  /** Why this task cannot be done yet (lesson order), or null. */
  locked: string | null;
  track: Track;
}

// ------------------------------------------------------------------------------------------------ learning path
export interface PublicQuestion { question: string; options: string[]; tier: number | null }
export interface ChoiceResult { correct: boolean; chosen: number; answer: number | null; explanation: string | null }
export interface QuizResult { passed: boolean; correct: number; total: number; pass_mark: number; results: ChoiceResult[]; attempts: number; completion: Completion | null }

export type LevelStatus = "completed" | "current" | "locked";
export interface PathLevel {
  index: number; tier: number; tier_name: string; skill: string; title: string; summary: string; topics: string[];
  status: LevelStatus; via: "exam" | "placement" | null; score: number | null; total: number | null;
}
export interface Progress {
  track: Track; track_label: string;
  level: { index: number; tier: number; tier_name: string; skill: string; title: string; summary: string; topics: string[]; lessons_required: number };
  levels_total: number; lessons_done: number; graduated: boolean;
  exam: {
    unlocked: boolean; attempts_today: number; attempts_max: number; pass_percent: number; questions: number;
    last: { correct: number; total: number; passed: boolean | null; submitted_at: string } | null; open_exam_id: string | null;
  };
  xp_total: number; xp_today: number; streak_days: number; knowledge_score: number;
  placement: { taken: boolean; available_today: boolean };
  path: PathLevel[];
}

export type ExamKind = "placement" | "level";
export interface Exam { id: string; kind: ExamKind; track: Track; level: number | null; level_title: string | null; questions: PublicQuestion[]; total: number; pass_mark: number | null }
export interface ExamResult {
  id: string; kind: ExamKind; track: Track; level: number | null; correct: number; total: number; passed: boolean | null; pass_mark: number | null;
  results: (ChoiceResult & { question: string; options: string[] })[];
  previous_level: number; new_level: number; new_level_title: string; xp: number;
  placement: { known_tier: number; start_level: number; tiers: { tier: number; name: string; correct: number; total: number }[] } | null;
}

export interface SkillMatch { skill_slug: string; skill_name: string; similarity: number; strength: "strong" | "moderate" | "weak" }

export interface FeedItem {
  id: string; title: string; url: string; source_slug: string; source_name: string; category: Category;
  published_at: string; summary: string | null; ai_summary: string | null; engagement: number | null;
  skill_match: SkillMatch | null; relevance: number; also_covered_by: string[];
}

export interface FeedPage { items: FeedItem[]; next_cursor: string | null; for_level: { index: number; title: string; tier_name: string; skill: string } | null }
export interface FeedQuery { track?: Track; category?: Category; skill?: string; sort?: "recent" | "relevance" | "for_you"; min_strength?: "moderate" | "strong"; cursor?: string; limit?: number }

// ------------------------------------------------------------------------------------------------ Phase 11: Bitcoin
export type BitcoinHorizonKey = "1d" | "3d" | "7d" | "14d";
export interface BitcoinPrice { price_usd: number; source: string; fetched_at: string }
export interface BitcoinNews {
  id: string; source_name: string; title: string; url: string; summary: string; btc_relevant: boolean; published_at: string;
}
export interface BitcoinHorizon {
  key: BitcoinHorizonKey; label: string; days: number; target_date: string; predicted_price: number;
  /** Forecast vs the price at prediction time, in percent. */
  change_pct: number;
  /** Probability (0-100) that the forecast DIRECTION is right. 50 = coin flip. */
  confidence: number;
  /** Cites headlines as [N#] (resolve via cited_news) and indicators as [T]. */
  reasoning: string;
  one_sigma_usd: number | null;
}
export interface BitcoinTechnicals {
  current_price: number; change_1d_pct: number | null; change_7d_pct: number | null; change_30d_pct: number | null;
  sma_7: number | null; sma_30: number | null; price_vs_sma7_pct: number | null; price_vs_sma30_pct: number | null;
  rsi_14: number | null; daily_sigma: number; annualised_vol_pct: number; high_30d: number | null; low_30d: number | null;
  one_sigma_move: Record<BitcoinHorizonKey, number>; days_of_history: number;
  macd: number | null; macd_signal: number | null; macd_hist: number | null; macd_hist_prev: number | null;
  bb_upper: number | null; bb_middle: number | null; bb_lower: number | null; bb_percent_b: number | null;
  bb_bandwidth_pct: number | null; bb_bandwidth_pctl: number | null;
  atr_14: number | null; atr_pct: number | null; ohlc_real: boolean;
}
export interface BitcoinPrediction {
  id: string; created_at: string; current_price: number; model: string; trigger: string;
  sentiment_score: number; sentiment_summary: string; technical_summary: string;
  horizons: BitcoinHorizon[];
  technicals: Partial<BitcoinTechnicals>;
  history: { date: string; close: number }[];
  cited_news: { ref: string; title: string; url: string; source: string }[];
}
export interface BitcoinRun {
  id: string; trigger: string; status: "queued" | "running" | "succeeded" | "failed"; error: string | null;
  created_at: string; started_at: string | null; finished_at: string | null;
  /** Queued for over 2 minutes: nothing is consuming the task queue. */
  stalled: boolean;
}
export interface BitcoinLatest { run: BitcoinRun | null; price: BitcoinPrice | null; prediction: BitcoinPrediction | null; news: BitcoinNews[]; disclaimer: string }

// ------------------------------------------------------------------------------------------------ Bitcoin bet advisor
export type BetAction = "BUY_YES" | "BUY_NO" | "HOLD_CASH" | "HALT";
export interface BetAdvice {
  id: string; created_at: string; action: BetAction; reason: string; shares: number; cost: number;
  /** `cost` is the stake in dollars. `entry_price` is the HIGHEST price per share worth paying (0-1); null when there is no bet. */
  entry_price: number | null; prob_win: number | null; kelly_full: number | null; fraction: number;
  take_profit: number | null; stop_loss: number | null; max_profit: number; expected_profit: number;
  inputs: { balance: number; strike: number; hours_to_settle: number; settle_at: string };
  market: { spot: number; spot_at: string };
  model: {
    forecast_at: string; forecast_age_hours: number; sigma_daily: number; stated_up_1d: number; shrink: number;
    calibrated_up_1d: number; calibration_note: string; p_yes: number;
  };
  bankroll: { balance: number; active: number; vault: number; peak: number; swept: number; pnl: number | null };
  warnings: string[];
}
export interface BetCheck {
  action: "SELL" | "HOLD"; rule: "exit" | "take_profit" | "stop_loss" | "hold"; reason: string; side: "BUY_YES" | "BUY_NO";
  sell_price: number; entry_price: number; prob_win_now: number; unrealized: number; shares: number; spot: number; strike: number; hours_left: number;
}
export interface BetOverview {
  /** The last check-in re-evaluated at the current BTC price. */
  live: { spot: number; p_yes: number; action: BetAction; changed: boolean; hours_left: number } | null;
  state: { balance: number; active: number; vault: number; peak: number; updated_at: string } | null;
  last: BetAdvice | null; open_position: boolean;
  history: { id: string; created_at: string; balance: number; vault: number; pnl: number | null; action: BetAction; shares: number;
             entry_price: number | null; cost: number; strike: number; is_reset: boolean }[];
  defaults: { hours_to_settle: number; settle_at: string; settle_label: string };
  params: { min_edge: number; kelly: number; max_fraction: number; vault_share: number; take_profit: number; stop_loss: number; min_trade: number; fee: number };
  disclaimer: string;
}
export interface BitcoinPulse {
  price_updated: boolean; news_added: number; forecast: "current" | "started" | "running" | "cooldown" | "busy" | "error";
  reason: string | null; run_id: string | null;
}
export interface BetInput { balance: number; strike?: number; hours_to_settle?: number; reset?: boolean }
