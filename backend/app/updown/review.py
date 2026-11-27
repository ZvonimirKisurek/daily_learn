"""Claude's plain-language review of the champion model, once a day: what the numbers mean, where it is weak, and
what would make it better. It reads the validation report; it never changes the model or the alert rule."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.db import get_pool
from app.updown import model as md

log = logging.getLogger(__name__)

SYSTEM = """You are a quantitative researcher reviewing a small model that predicts Polymarket's 5- and 15-minute BTC Up/Down
markets, for its owner, who is learning machine learning. You get its validation report (newest windows held out by time),
the two free baselines (the market's own price, and a random-walk formula), and a backtest of the alert rule.

Write 120-220 words of plain prose, no headings or lists: (1) does it beat the market, and by how much in words a beginner
understands (log loss, accuracy, calibration of its >= 90% calls); (2) is the backtest meaningful or too small to trust;
(3) the single most useful next improvement. Be blunt: if it is not better than the market, say betting on it is
gambling. Never promise profit. Use only numbers from the report."""


def maybe_review(settings: Settings | None = None, *, min_hours: float = 20) -> str | None:
    s = settings or get_settings()
    ch = md.champion()
    if ch is None or ch["review"]:
        return None
    with get_pool().connection() as conn:
        last = conn.execute("select max(created_at) as at from updown_models where review is not null").fetchone()["at"]
    if last and last > datetime.now(timezone.utc) - timedelta(hours=min_hours):
        return None
    try:
        from app.curriculum.llm import make_llm

        text = make_llm(s).generate_text(
            system=SYSTEM, user=f"<report>{json.dumps(ch['metrics'], default=str)}</report>\n\nWrite the review.",
            check=lambda t: [] if 80 <= len(t.split()) <= 300 else ["write 120-220 words"], purpose="ModelReview",
            temperature=0.3, max_tokens=900).strip()
    except Exception:  # noqa: BLE001 - the model works without a review
        log.exception("updown: Claude review failed")
        return None
    with get_pool().connection() as conn:
        conn.execute("update updown_models set review = %s where version = %s", (text, ch["version"]))
    return text
