"""BitcoinPredictionOutput: the contract between the forecasting LLM and the database.

As in curriculum/schemas.py, there are two layers. (1) Strict JSON-Schema decoding guarantees the SHAPE: every field is
present and has the right type. (2) The validators below check the VALUES, and any failure goes back to the model as a
repair prompt (StructuredLLM.generate). A prediction is stored only if it passes all of these checks:

  a. Closed-world citations. Headlines appear in the prompt as N1..N30. Every [N#] tag in the reasoning must be one we
     showed; the model can't invent a "Reuters report" because it can only point at ids we supplied. Each horizon's
     reasoning must also cite at least one technical fact with the tag [T].
  b. Volatility band. |ln(predicted / current)| <= k · σ_daily · √h, with σ measured from real prices (technicals.py).
     With k=3, the forecast must be within a 3-sigma move. That band contains about 99.7% of random-walk outcomes, so a
     forecast outside it is almost certainly not an analysis and is probably a hallucinated number. This is the main
     anti-hallucination device for the numbers themselves.
  c. Calibration. Confidence has one fixed definition (below) and is capped (default 85).
  d. No financial advice. Reasoning that tells the reader to act (buy/sell, go long, use stop-losses, leverage...) is
     rejected. The model may describe market behaviour ("selling pressure", "buyers stepped in"), but it may not
     recommend an action.

WHAT "CONFIDENCE" MEANS (the same definition appears in the prompt): the probability, 0-100, that on the target date
the actual price will be on the same side of today's price as the forecast, i.e. that the forecast DIRECTION is right.
50 means a coin flip. A fixed definition makes the number measurable: after 14 days you can check whether "70%"
forecasts were right about 70% of the time. A definition-free "confidence" is decoration.

Field ORDER matters. Models generate left to right, so the analysis fields (sentiment, technicals) come first and the
numbers come last: the model commits to its reading of the evidence before it writes a price, rather than writing a price
and then justifying it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.bitcoin.technicals import HORIZONS

CITATION = re.compile(r"\[(N\d{1,3}|T)\]")
NEWS_TAG = re.compile(r"\[(N\d{1,3})\]")
# Patterns that turn analysis into advice. Descriptive market vocabulary ("sell-off", "buyers", "long liquidations") is
# allowed on purpose; telling the reader what to do is not.
ADVICE = re.compile(
    r"\b(?:you|investors|traders|readers)\s+(?:should|must|ought to|need to)\b"
    r"|\b(?:we|i)\s+(?:recommend|suggest|advise)\b"
    r"|\b(?:buy|sell|short)\s+(?:now|today|the dip|the rip|bitcoin|btc)\b"
    r"|\b(?:go|going)\s+(?:long|short)\b"
    r"|\b(?:strong\s+)?(?:buy|sell)\s+(?:signal|rating|opportunity)\b"
    r"|\b(?:stop[- ]loss|take[- ]profit|position[- ]siz\w*|leverage[ds]?|allocate|accumulate)\b"
    r"|\bgood (?:time|entry|moment) to\b",
    re.I,
)


@dataclass(frozen=True)
class BitcoinGrounding:
    """Facts the validators check against. Passed to model_validate(..., context=...)."""

    news_ids: frozenset[str]  # "N1".."Nk" shown in the prompt
    current_price: float
    daily_sigma: float
    band_sigmas: float = 3.0
    max_confidence: int = 85


def _g(info: ValidationInfo) -> BitcoinGrounding | None:
    return info.context if isinstance(info.context, BitcoinGrounding) else None


def _check_text(v: str, info: ValidationInfo, *, need_technical: bool) -> str:
    g = _g(info)
    if ADVICE.search(v):
        raise ValueError(f"contains investment advice ({ADVICE.search(v).group(0)!r}): describe the market, never tell the reader what to do")
    if g is None:
        return v
    unknown = sorted({m for m in NEWS_TAG.findall(v) if m not in g.news_ids})
    if unknown:
        raise ValueError(f"cites unknown news ids {unknown}; use only ids that appear in <news>")
    if need_technical and "[T]" not in v:
        raise ValueError("must cite at least one technical fact from <market_data> with the tag [T]")
    if need_technical and g.news_ids and not NEWS_TAG.search(v):
        raise ValueError("must cite at least one headline from <news> with its [N#] tag (or explicitly say the news is not informative and cite the most relevant one)")
    return v


Reasoning = Field(min_length=80, max_length=900, description="2-4 sentences. Cite news as [N#] and technical facts as [T].")
Confidence = Field(ge=0, le=100, description="Probability (0-100) that the price on the target date is on the SAME SIDE of today's price as your forecast. 50 = coin flip.")
Price = Field(gt=0, description="Forecast BTC price in USD on the target date.")


class BitcoinPredictionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- 1. analysis first -------------------------------------------------------------------------------
    sentiment_summary: str = Field(min_length=80, max_length=900, description="What the news collectively implies for BTC, citing [N#].")
    sentiment_score: int = Field(ge=-100, le=100, description="-100 very bearish .. 0 neutral .. +100 very bullish, from the news only.")
    technical_summary: str = Field(min_length=80, max_length=900, description="What the indicators in <market_data> say, citing [T].")

    # --- 2. then the numbers -----------------------------------------------------------------------------
    reasoning_1d: str = Reasoning
    predicted_price_1d: float = Price
    confidence_1d: int = Confidence

    reasoning_3d: str = Reasoning
    predicted_price_3d: float = Price
    confidence_3d: int = Confidence

    reasoning_7d: str = Reasoning
    predicted_price_7d: float = Price
    confidence_7d: int = Confidence

    reasoning_14d: str = Reasoning
    predicted_price_14d: float = Price
    confidence_14d: int = Confidence

    @field_validator("sentiment_summary")
    @classmethod
    def _sentiment(cls, v: str, info: ValidationInfo) -> str:
        return _check_text(v, info, need_technical=False)

    @field_validator("technical_summary")
    @classmethod
    def _technical(cls, v: str, info: ValidationInfo) -> str:
        _check_text(v, info, need_technical=False)
        if _g(info) is not None and "[T]" not in v:
            raise ValueError("must cite the indicators with the tag [T]")
        return v

    @field_validator("reasoning_1d", "reasoning_3d", "reasoning_7d", "reasoning_14d")
    @classmethod
    def _reasoning(cls, v: str, info: ValidationInfo) -> str:
        return _check_text(v, info, need_technical=True)

    @model_validator(mode="after")
    def _numbers(self, info: ValidationInfo) -> "BitcoinPredictionOutput":
        g = _g(info)
        if g is None:
            return self
        problems = []
        for key, days in HORIZONS:
            price = getattr(self, f"predicted_price_{key}")
            conf = getattr(self, f"confidence_{key}")
            limit = g.band_sigmas * g.daily_sigma * math.sqrt(days)
            move = math.log(price / g.current_price)
            if abs(move) > limit:
                lo, hi = g.current_price * math.exp(-limit), g.current_price * math.exp(limit)
                problems.append(f"predicted_price_{key}={price:.0f} is a {move * 100:+.1f}% move, outside the plausible "
                                f"{g.band_sigmas:g}-sigma range {lo:.0f}..{hi:.0f} for {days} day(s)")
            if conf > g.max_confidence:
                problems.append(f"confidence_{key}={conf} exceeds the calibration cap of {g.max_confidence}")
            if abs(price - g.current_price) < 0.01:
                problems.append(f"predicted_price_{key} equals today's price: commit to a direction (confidence 50 if you see no edge)")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def horizon(self, key: str) -> tuple[float, int, str]:
        return getattr(self, f"predicted_price_{key}"), getattr(self, f"confidence_{key}"), getattr(self, f"reasoning_{key}")
