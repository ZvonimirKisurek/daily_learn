"""The daily Bitcoin forecast: ingest, compute indicators, score past forecasts, prompt, validate, store.

    ingest_price ─┐
    ingest_news  ─┼─► technicals.compute (code) ──┐
    daily OHLC   ─┘                                ├─► build_bitcoin_prompt ─► StructuredLLM.generate(BitcoinPredictionOutput,
    past forecasts ─► accuracy.get_past_accuracy ─┘                            context=BitcoinGrounding) ─► insert row
                                                                                                              │
    (the stored row is scored by get_past_accuracy once its horizons pass: the self-correction loop) ◄────────┘

Failure policy: no price means no forecast (PriceUnavailable propagates). Missing history or news degrades the inputs
(fewer indicators, no headlines) and the prompt says so explicitly, so the model leans on the base rate instead of
inventing context. A failure to score past forecasts only drops the track record from the prompt. Every attempt, including repair attempts, counts against LLM_DAILY_BUDGET_USD through the shared
usage ledger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from app.bitcoin import accuracy, confluence, ingest, technicals
from app.bitcoin.schemas import BitcoinGrounding, BitcoinPredictionOutput
from app.bitcoin.technicals import HORIZONS
from app.config import Settings, get_settings
from app.curriculum.errors import LLMValidationFailed
from app.curriculum.prompts import BITCOIN_PREDICTION_SYSTEM, build_bitcoin_prompt
from app.db import get_pool

log = logging.getLogger(__name__)


def describe_failure(exc: BaseException) -> str:
    """A short, user-facing reason for the dashboard. Never echoes request payloads or keys."""
    import openai

    from app.curriculum.errors import LLMBudgetExceeded, LLMRefusal, LLMTruncated

    if isinstance(exc, ingest.PriceUnavailable):
        return "Price feeds unreachable (CoinGecko and Coinbase both failed). No forecast is made without a real price."
    if isinstance(exc, LLMBudgetExceeded):
        if "Claude usage limit" in str(exc):
            return "Your Claude plan's usage limit is reached for now. Forecasts resume when it resets."
        return "Daily AI budget reached (LLM_DAILY_BUDGET_USD). The next run will try again."
    if isinstance(exc, LLMValidationFailed):
        # Say WHICH rules failed: "field: problem" lines from the last attempt, minus Pydantic's "Value error, " prefix.
        lines = [ln.lstrip("- ").replace("Value error, ", "") for ln in (exc.errors or "").splitlines() if ln.strip()]
        detail = "; ".join(lines[:3]) + (f" (+{len(lines) - 3} more)" if len(lines) > 3 else "")
        return ("The model's forecast kept failing validation after repairs"
                + (f": {detail}" if detail else ".") + " The raw model output is in the worker log.")
    if isinstance(exc, (LLMRefusal, LLMTruncated)):
        return f"The model did not return a usable forecast ({exc.__class__.__name__})."
    if isinstance(exc, openai.AuthenticationError):
        return "OpenAI rejected the API key (OPENAI_API_KEY)."
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError, openai.RateLimitError)):
        return "OpenAI is unreachable or rate-limiting right now. Try again in a few minutes."
    return f"{exc.__class__.__name__}: {str(exc)[:240]}"


def _mark_run(run_id, **cols) -> None:
    try:  # status bookkeeping must never be the reason a forecast fails
        sets = ", ".join(f"{k} = %s" for k in cols)
        with get_pool().connection() as conn:
            conn.execute(f"update bitcoin_runs set {sets} where id = %s", [*cols.values(), run_id])
    except Exception:  # noqa: BLE001
        log.exception("could not update bitcoin run %s", run_id)


def generate_prediction(*, trigger: str = "beat", llm=None, settings: Settings | None = None, ingest_news: bool = True,
                        run_id: str | None = None) -> dict:
    """Run a forecast and record its status in bitcoin_runs. The API creates the run (status `queued`) and passes
    `run_id`; scheduled and CLI runs create their own row here."""
    if run_id is None:
        try:
            with get_pool().connection() as conn:
                run_id = conn.execute("insert into bitcoin_runs (trigger, status, started_at) values (%s, 'running', now()) returning id",
                                      (trigger,)).fetchone()["id"]
        except Exception:  # noqa: BLE001
            log.exception("could not create bitcoin run row")
    else:
        _mark_run(run_id, status="running", started_at=datetime.now(timezone.utc), error=None)
    try:
        result = _predict(trigger=trigger, llm=llm, settings=settings, ingest_news=ingest_news)
    except Exception as exc:
        if run_id:
            _mark_run(run_id, status="failed", error=describe_failure(exc)[:1000], finished_at=datetime.now(timezone.utc))
        raise
    if run_id:
        _mark_run(run_id, status="succeeded", prediction_id=result["id"], finished_at=datetime.now(timezone.utc))
    return result | {"run_id": str(run_id) if run_id else None}


def _predict(*, trigger: str, llm, settings: Settings | None, ingest_news: bool) -> dict:
    s = settings or get_settings()
    if llm is None:
        from app.curriculum.llm import make_llm

        llm = make_llm(s)

    # 1. Data. News failures are logged and tolerated; a price failure aborts.
    price_row = ingest.ingest_price(s)
    current = float(price_row["price_usd"])
    if ingest_news:
        try:
            ingest.ingest_news(s)
        except Exception:  # noqa: BLE001 - stale news is better than no forecast
            log.exception("bitcoin news ingestion failed; forecasting with what is already stored")
    history = ingest.fetch_daily_ohlc(s.bitcoin_history_days, s)
    tech = technicals.compute(history, current)
    candles = technicals.candles_with_spot(history, current)

    # The feedback loop: how did the forecasts whose target date has now passed actually do?
    try:
        track = accuracy.get_past_accuracy({c.day: c.close for c in history}, window_days=s.bitcoin_accuracy_window_days,
                                           tolerance_hours=s.bitcoin_accuracy_tolerance_hours)
    except Exception:  # noqa: BLE001 - a forecast without a report card beats no forecast
        log.exception("bitcoin accuracy scoring failed; forecasting without a track record")
        track = None

    news = ingest.recent_news(s.bitcoin_news_window_hours, s.bitcoin_prompt_news, s.bitcoin_max_per_source)
    for i, n in enumerate(news, 1):
        n["ref"] = f"N{i}"
        n["published_at"] = n["published_at"].astimezone(timezone.utc)

    # 2. Prompt + validated generation.
    td = tech.as_dict()
    grounding = BitcoinGrounding(news_ids=frozenset(n["ref"] for n in news), current_price=current, daily_sigma=tech.daily_sigma,
                                 band_sigmas=s.bitcoin_band_sigmas, max_confidence=s.bitcoin_max_confidence,
                                 require_advanced=tech.macd is not None or tech.bb_upper is not None,
                                 tech_signals=confluence.technical_signals(td), accuracy=track)
    user_prompt = build_bitcoin_prompt(td, candles[-30:], news, accuracy=track, band_sigmas=s.bitcoin_band_sigmas)
    try:
        # temperature = settings.temp_bitcoin (0.4): the confluence rules are arithmetic, so less sampling noise helps
        out: BitcoinPredictionOutput = llm.generate(system=BITCOIN_PREDICTION_SYSTEM, user=user_prompt,
                                                    output_model=BitcoinPredictionOutput, grounding=grounding, temperature=s.temp_bitcoin)
    except LLMValidationFailed as exc:
        # Every rejected reply was already logged attempt by attempt (StructuredLLM.generate). This line ties them to the
        # inputs, so the log alone answers "what did the model see, and what did it write?".
        log.error("btc forecast failed validation after %d attempts | price=%.2f daily_sigma=%.4f band=%gσ news_ids=%s\n"
                  "last errors:\n%s\n----- last raw model output -----\n%s\n----- end -----",
                  len(exc.raw_outputs), current, tech.daily_sigma, s.bitcoin_band_sigmas,
                  ",".join(n["ref"] for n in news) or "none", exc.errors, (exc.raw_outputs or [""])[-1][:12_000])
        raise

    # 3. Store, with an exact record of what the model saw.
    model_label = s.llm_model if s.llm_provider == "openai" else f"claude ({s.claude_code_model or 'plan default'})"
    inputs = {
        "technicals": td,
        "history": [{"date": c.day.isoformat(), "open": round(c.open, 2), "high": round(c.high, 2), "low": round(c.low, 2),
                     "close": round(c.close, 2)} for c in candles[-30:]],
        "accuracy": track,
        "news": [{"ref": n["ref"], "id": str(n["id"]), "title": n["title"], "url": n["url"], "source": n["source_name"]} for n in news],
    }
    cols = {"price_id": price_row["id"], "current_price": round(current, 2), "model": model_label, "trigger": trigger, "inputs": Jsonb(inputs),
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
