from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Looks for .env in backend/ first, then the repo root.
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql://postgres:postgres@localhost:5432/mlesprint"
    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embed_batch_size: int = 64

    user_agent: str = "MLE-Sprint/0.1 (personal research aggregator; contact: you@example.com)"
    http_timeout: float = 20.0
    http_max_retries: int = 4
    host_min_interval: float = 1.0  # seconds between requests to the same host
    fetch_concurrency: int = 8

    reddit_client_id: str = ""
    reddit_client_secret: str = ""

    dedup_threshold: float = 0.92  # cosine similarity => "same story"
    dedup_window_days: int = 14  # only treat as duplicate if the canonical article is this recent
    max_item_age_days: int = 30
    default_max_items: int = 30

    # Circuit breaker: after N consecutive failures only retry the source once per `cooldown` hours.
    breaker_failures: int = 5
    breaker_cooldown_hours: int = 24

    sources_file: str = "sources/sources.yaml"

    # --- LLM / curriculum engine ---
    llm_model: str = "gpt-4o"  # must support Structured Outputs (gpt-4o-2024-08-06 or later). Check your account's model list
    llm_max_tokens: int = 8000  # max completion tokens per call (gpt-4o allows up to 16k)
    llm_seed: int | None = None  # set for best-effort reproducible outputs
    # Sampling temperature per task (OpenAI chat models accept it; reasoning models such as o-series/gpt-5 do not).
    temp_weekly: float = 0.6  # planning a theme benefits from some variety
    temp_daily: float = 0.3  # stay close to the evidence
    temp_summary: float = 0.2  # near-deterministic restating
    temp_chapter: float = 0.7  # teaching prose: loose enough to elaborate at length (0.5 made GPT-4o summarise)
    temp_eval: float = 0.1  # grading should be as repeatable as possible

    # --- deep learning mode ---
    llm_chapter_max_tokens: int = 12000  # a 2,000+ word chapter with code needs ~4-6k tokens; leave headroom
    chapter_min_words: int = 1500  # prose words (outside code fences) the chapter must reach
    essay_min_words: int = 450
    essay_max_words: int = 800
    max_submission_attempts: int = 6  # graded attempts per task (each one is an LLM call)
    max_submission_chars: int = 20000
    llm_max_repairs: int = 3  # re-prompts after a validation failure before giving up
    llm_daily_budget_usd: float = 1.0  # hard cap on estimated LLM spend per rolling 24h (typical use is ~$0.15/day)
    llm_sdk_retries: int = 5  # SDK-level exponential backoff on 429/5xx/connection errors
    prompt_article_count: int = 50  # articles injected into the prompt
    candidate_window_days: int = 7
    max_per_source: int = 6  # diversity cap so one firehose (arXiv) can't fill the prompt
    min_candidates: int = 10
    # --- AI summaries (cheap, high-volume => small model) ---
    summary_model: str = "gpt-4o-mini"
    summary_batch_size: int = 10
    summary_max_per_run: int = 60

    # --- API ---
    api_token: str = ""  # if set, every /api request needs "Authorization: Bearer <token>". Comma-separate to rotate: "new,old"
    enable_docs: bool = True  # /docs, /redoc, /openapi.json. The Docker image turns this off.
    cors_origins: str = "http://localhost:3000"  # comma-separated
    frontend_url: str = ""  # e.g. https://mle-sprint.vercel.app ; added to the CORS allow-list so prod needs one variable, not a list
    db_pool_size: int = 10
    match_strong: float = 0.40  # cosine(article, skill) thresholds for the UI badge; calibrate with `cli feed-stats`
    match_moderate: float = 0.28

    # "celery": enqueue to a worker via Redis. "inline": run in a background thread of the API process
    # (single-user deployments with no Redis; jobs are lost if the process restarts mid-run - just click again).
    task_backend: str = "celery"
    # With TASK_BACKEND=celery: if Redis is unreachable when a button is pressed, run that job inline (in the API process)
    # instead of failing. Scheduled jobs are unaffected. Set false to get a clean 503 instead.
    task_inline_fallback: bool = True

    # --- 4-week roadmap steering (off unless ROADMAP_START is set to the Monday you begin) ---
    roadmap_file: str = "curriculum/roadmap.yaml"
    roadmap_start: str = ""  # YYYY-MM-DD

    # --- retention (keeps a free-tier database small) ---
    retention_days: int = 60
    duplicate_retention_days: int = 7

    timezone: str = "UTC"  # IANA name; drives "today", the Sunday-night/morning schedules

    # --- Phase 11: Bitcoin intelligence ---
    bitcoin_sources_file: str = "sources/bitcoin_sources.yaml"
    bitcoin_history_days: int = 60  # daily closes fetched for the indicators (RSI-14 and SMA-30 need at least 31)
    bitcoin_news_window_hours: int = 72  # only news this recent reaches the prompt
    bitcoin_prompt_news: int = 30  # max headlines in the prompt (~150 tokens each)
    bitcoin_max_per_source: int = 8  # so one prolific feed can't fill the prompt
    bitcoin_max_confidence: int = 85  # validator cap: no honest model is >85% sure where BTC will be next week
    bitcoin_band_sigmas: float = 4.0  # a forecast must sit within ±k·σ·√h of today's price (see bitcoin/schemas.py)
    bitcoin_regen_cooldown_minutes: int = 10  # the manual "Refresh" can't trigger paid runs faster than this
    bitcoin_news_retention_days: int = 30
    temp_bitcoin: float = 0.2  # analysis, not creativity: keep the numbers stable run to run


    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in [*self.cors_origins.split(","), self.frontend_url] if o.strip()]
        return list(dict.fromkeys(origins))  # browsers send Origin without a trailing slash; de-duplicate, keep order


@lru_cache
def get_settings() -> Settings:
    return Settings()
