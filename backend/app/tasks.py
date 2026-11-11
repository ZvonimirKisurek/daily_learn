import logging

import openai
import psycopg

from app.celery_app import app

log = logging.getLogger(__name__)


@app.task(
    bind=True, name="app.tasks.ingest_all",
    autoretry_for=(psycopg.OperationalError,), retry_backoff=True, retry_backoff_max=600, max_retries=3,
)
def ingest_all(self) -> dict:
    from app.ingestion.pipeline import run_ingestion

    return run_ingestion(trigger="beat")

# Transient infrastructure errors are retried with exponential backoff. LLMValidationFailed / LLMRefusal /
# LLMBudgetExceeded (incl. 'insufficient_quota', which StructuredLLM converts) / NotEnoughData are NOT retried here:
# repeating the identical request will not change the outcome.
_TRANSIENT = (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError, psycopg.OperationalError)


@app.task(bind=True, name="app.tasks.generate_weekly_theme", autoretry_for=_TRANSIENT, retry_backoff=True, retry_backoff_max=900, max_retries=4)
def generate_weekly_theme_task(self) -> dict:
    from app.curriculum.generators import generate_weekly_theme

    r = generate_weekly_theme()
    return {"week_start": r["week_start"], "title": r["payload"]["title"], "cached": r["cached"]}


@app.task(bind=True, name="app.tasks.generate_daily_briefing", autoretry_for=_TRANSIENT, retry_backoff=True, retry_backoff_max=900, max_retries=4)
def generate_daily_briefing_task(self, track: str = "ai") -> dict:
    from app.curriculum.generators import NotEnoughData, generate_daily_briefing

    try:
        r = generate_daily_briefing(track=track)
    except NotEnoughData as exc:
        if track == "ai":
            raise
        log.warning("no %s lesson today: %s", track, exc)  # a new track has no articles until its sources were ingested
        return {"track": track, "skipped": str(exc)}
    return {"day": r["day"], "track": track, "cached": r["cached"]}


@app.task(bind=True, name="app.tasks.summarize_recent_articles", autoretry_for=_TRANSIENT, retry_backoff=True, max_retries=3)
def summarize_recent_articles_task(self) -> dict:
    from app.curriculum.summaries import summarize_pending

    return summarize_pending()


@app.task(
    bind=True, name="app.tasks.generate_bitcoin_daily_insight",
    autoretry_for=_TRANSIENT, retry_backoff=True, retry_backoff_max=900, max_retries=3,
)
def generate_bitcoin_daily_insight(self, trigger: str = "beat", run_id: str | None = None) -> dict:
    """09:00 daily (see celery_app.py) and the dashboard's Refresh button: price, news, indicators, then the AI forecast.
    PriceUnavailable is not retried by Celery (CoinGecko and Coinbase already retried and both failed). The next
    day's run, or a manual refresh, tries again."""
    from app.bitcoin.predictor import generate_prediction
    from app.config import get_settings

    if trigger == "beat" and not get_settings().bitcoin_background_forecast:
        # Forecasts are made on demand while the Bitcoin page is open (bitcoin/live.py), not on a timer.
        return {"skipped": "background forecast is off (BITCOIN_BACKGROUND_FORECAST=false)"}
    return generate_prediction(trigger=trigger, run_id=run_id)


@app.task(name="app.tasks.prune_old_data")
def prune_old_data_task() -> dict:
    from app.maintenance import prune

    return prune()
