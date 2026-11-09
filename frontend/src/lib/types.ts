/** Mirrors backend/app/api/schemas.py. */
export type TaskKind = "reading" | "hands_on" | "insight";
export type Category = "research" | "industry_news" | "engineering_blog" | "community" | "newsletter" | "researcher";

export interface ArticleRef { article_uuid: string; ref_id?: string | null; title: string; url: string; source: string; category: string }

export interface Task {
  id: string; kind: TaskKind; skill_slug: string; difficulty: number; title: string;
  completed: boolean; completed_at: string | null; points_awarded: number | null;
  attempt_count: number; evaluation_score: number | null; evaluation_passed: boolean | null; has_content: boolean;
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
  word_count: number; section_headings: string[]; inspiration: ArticleRef[];
}
export interface InsightMeta {
  headline: string; thesis: string; skill_slug: string; body: string; word_count: number; evidence: ArticleRef[];
}

export interface Briefing {
  id: string; day: string; completed_at: string | null; tasks: Task[];
  payload: { morning_reading: ReadingMeta; hands_on_task: HandsOnSpec; market_insight: InsightMeta };
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
  slug: string; name: string; level: number; target: number; percent_to_target: number; delta_7d: number;
  history: { at: string; level: number }[];
}

export interface Today { date: string; state: "ready" | "pending"; briefing: Briefing | null; weekly_theme: WeeklyTheme | null; skills: Skill[] }

export interface Completion {
  task: Task; skill: { slug: string; level: number; delta: number }; briefing_completed: boolean; already_completed: boolean;
}

export interface CriterionResult { criterion: string; points: number; max_points: number; evidence: string }
export interface Evaluation {
  criteria: CriterionResult[]; score: number; passed: boolean; summary: string; strengths: string[]; problems: string[];
  how_to_improve: string[]; feedback_markdown: string;
}
export interface SubmissionResult {
  task: Task; evaluation: Evaluation; passed: boolean; skill: { slug: string; level: number; delta: number } | null;
  briefing_completed: boolean; attempts_used: number; attempts_max: number;
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
}

export interface SkillMatch { skill_slug: string; skill_name: string; similarity: number; strength: "strong" | "moderate" | "weak" }

export interface FeedItem {
  id: string; title: string; url: string; source_slug: string; source_name: string; category: Category;
  published_at: string; summary: string | null; ai_summary: string | null; engagement: number | null;
  skill_match: SkillMatch | null; relevance: number; also_covered_by: string[];
}

export interface FeedPage { items: FeedItem[]; next_cursor: string | null }
export interface FeedQuery { category?: Category; skill?: string; sort?: "recent" | "relevance"; min_strength?: "moderate" | "strong"; cursor?: string; limit?: number }

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
