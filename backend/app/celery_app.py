import ssl

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()
# No result backend: nothing in this app ever reads a task's return value (the API only enqueues; tasks write to Postgres).
# Dropping it removes a Redis write + expiry per task, which matters on pay-per-command Redis such as Upstash.
app = Celery("mle_sprint", broker=settings.redis_url, include=["app.tasks"])
app.conf.update(
    task_ignore_result=True,
    # Managed Redis (Upstash, Render Key Value, ElastiCache...) is TLS-only: the URL scheme is rediss://. Celery refuses a
    # rediss:// URL without an explicit certificate policy, so supply one here instead of making you hand-edit the URL.
    **({"broker_use_ssl": {"ssl_cert_reqs": ssl.CERT_REQUIRED}} if settings.redis_url.startswith("rediss://") else {}),
    broker_connection_retry_on_startup=True,  # keep retrying if Redis is briefly unreachable while the container boots
    broker_transport_options={"visibility_timeout": 3600, "socket_keepalive": True, "socket_timeout": 30,
                              "socket_connect_timeout": 10, "retry_on_timeout": True},
    worker_enable_remote_control=False,  # no pidbox/inspect broadcast channel: fewer idle Redis commands, nothing here uses it
    worker_send_task_events=False,
    broker_heartbeat=0,
    timezone=settings.timezone,  # beat schedules below are interpreted in the learner's timezone
    task_acks_late=True,  # re-deliver if a worker dies mid-run (ingestion is idempotent via url_hash)
    worker_prefetch_multiplier=1,
    task_time_limit=60 * 60,
    beat_schedule={
        "ingest-every-6h": {"task": "app.tasks.ingest_all", "schedule": crontab(minute=0, hour="*/6")},
        # 20 minutes after each ingestion run, summarise whatever it brought in.
        "summarize-after-ingest": {"task": "app.tasks.summarize_recent_articles", "schedule": crontab(minute=20, hour="*/6")},
        "prune-weekly": {"task": "app.tasks.prune_old_data", "schedule": crontab(minute=15, hour=3, day_of_week="mon")},
        "weekly-theme-sunday-night": {"task": "app.tasks.generate_weekly_theme", "schedule": crontab(minute=0, hour=22, day_of_week="sun")},
        # 06:30 so the 06:00 ingestion run (which feeds the candidate pool) has finished.
        # Monday to Friday only: weekends are rest days (the task also refuses them, see generators.RestDay).
        "daily-briefing-morning": {"task": "app.tasks.generate_daily_briefing", "schedule": crontab(minute=30, hour=6, day_of_week="mon-fri")},
        # The management track's lesson, ten minutes later so the two never compete for the same LLM budget window.
        "daily-management-lesson": {"task": "app.tasks.generate_daily_briefing", "schedule": crontab(minute=40, hour=6, day_of_week="mon-fri"),
                                    "kwargs": {"track": "management"}},
        # Phase 11: BTC price + news + AI forecast at 09:00 in `timezone` above (the TIMEZONE env var, default UTC).
        # The 06:00 general ingestion has finished by then; the Bitcoin task fetches its own feeds anyway.
        # The price at every full hour, so yesterday's bet advice can be scored against the price at settlement.
        "bitcoin-hourly-price": {"task": "app.tasks.record_bitcoin_price", "schedule": crontab(minute=0)},
        "bitcoin-daily-insight": {"task": "app.tasks.generate_bitcoin_daily_insight", "schedule": crontab(minute=0, hour=9)},
    },
)
