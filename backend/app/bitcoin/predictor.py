"""The daily Bitcoin forecast: ingest, compute indicators, prompt, validate, store.

    ingest_price ─┐
    ingest_news  ─┼─► technicals.compute (code) ─► build_bitcoin_prompt ─► StructuredLLM.generate(BitcoinPredictionOutput,
    daily closes ─┘                                                         context=BitcoinGrounding) ─► insert row

Failure policy: no price means no forecast (PriceUnavailable propagates). Missing history or news degrades the inputs
(fewer indicators, no headlines) and the prompt says so explicitly, so the model leans on the base rate instead of
inventing context. Every attempt, including repair attempts, counts against LLM_DAILY_BUDGET_USD through the shared
usage ledger.
"""
from __future__ import annotations

import logging
from datetime import timezone

from psycopg.types.json import Jsonb

from app.bitcoin import ingest, technicals
from app.bitcoin.schemas import BitcoinGrounding, BitcoinPredictionOutput
from app.bitcoin.technicals import HORIZONS
from app.config import Settings, get_settings
from app.curriculum.prompts import BITCOIN_PREDICTION_SYSTEM, build_bitcoin_prompt
from app.db import get_pool

log = logging.getLogger(__name__)


def generate_prediction(*, trigger: str = "beat", llm=None, settings: Settings | None = None, ingest_news: bool = True) -> dict:
    s = settings or get_settings()
    if llm is None:
        from app.curriculum.llm import StructuredLLM

        llm = StructuredLLM(s)

    # 1. Data. News failures are logged and tolerated; a price failure aborts.
    price_row = ingest.ingest_price(s)
    current = float(price_row["price_usd"])
    if ingest_news:
        try:
            ingest.ingest_news(s)
        except Exception:  # noqa: BLE001 - stale news is better than no forecast
            log.exception("bitcoin news ingestion failed; forecasting with what is already stored")
    history = ingest.fetch_daily_closes(s.bitcoin_history_days, s)
    tech = technicals.compute(history, current)

    news = ingest.recent_news(s.bitcoin_news_window_hours, s.bitcoin_prompt_news, s.bitcoin_max_per_source)
    for i, n in enumerate(news, 1):
        n["ref"] = f"N{i}"
        n["published_at"] = n["published_at"].astimezone(timezone.utc)

    # 2. Prompt + validated generation.
    td = tech.as_dict()
    series = technicals.with_spot(history, current)
    last7 = [(d.isoformat(), c) for d, c in series[-7:]]
    grounding = BitcoinGrounding(news_ids=frozenset(n["ref"] for n in news), current_price=current, daily_sigma=tech.daily_sigma,
                                 band_sigmas=s.bitcoin_band_sigmas, max_confidence=s.bitcoin_max_confidence)
    out: BitcoinPredictionOutput = llm.generate(system=BITCOIN_PREDICTION_SYSTEM, user=build_bitcoin_prompt(td, last7, news),
                                                output_model=BitcoinPredictionOutput, grounding=grounding, temperature=s.temp_bitcoin)

    # 3. Store, with an exact record of what the model saw.
    inputs = {
        "technicals": td,
        "history": [{"date": d.isoformat(), "close": round(c, 2)} for d, c in series[-30:]],
        "news": [{"ref": n["ref"], "id": str(n["id"]), "title": n["title"], "url": n["url"], "source": n["source_name"]} for n in news],
    }
    cols = {"price_id": price_row["id"], "current_price": round(current, 2), "model": s.llm_model, "trigger": trigger, "inputs": Jsonb(inputs),
            "sentiment_score": out.sentiment_score, "sentiment_summary": out.sentiment_summary, "technical_summary": out.technical_summary}
    for key, _ in HORIZONS:
        price, conf, why = out.horizon(key)
        cols |= {f"predicted_price_{key}": round(price, 2), f"confidence_{key}": conf, f"reasoning_{key}": why}
    names = ", ".join(cols)
    with get_pool().connection() as conn:
        row = conn.execute(f"insert into bitcoin_predictions ({names}) values ({', '.join(['%s'] * len(cols))}) returning id, created_at",
                           list(cols.values())).fetchone()
    log.info("btc prediction %s: now=%.0f 1d=%.0f(%d%%) 14d=%.0f(%d%%)", row["id"], current, out.predicted_price_1d, out.confidence_1d,
             out.predicted_price_14d, out.confidence_14d)
    return {"id": str(row["id"]), "created_at": row["created_at"].isoformat(), "current_price": current, "news_used": len(news)}
