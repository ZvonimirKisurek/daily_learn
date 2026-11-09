"""BitcoinPredictionOutput: the contract between the forecasting LLM and the database.

As in curriculum/schemas.py, there are two layers. (1) Strict JSON-Schema decoding guarantees the SHAPE: every field is
present and has the right type. (2) The validators below check the VALUES, and any failure goes back to the model as a
repair prompt (StructuredLLM.generate).

The rules come in two kinds: what we NORMALISE (cosmetic slips are fixed silently, with no repair round-trip) and what we
REJECT (a real failure the model has to correct).

  NORMALISED (cosmetic)
  - Citation spelling. The canonical forms are [N4] and [T], which the frontend turns into links and chips. Models also
    write [N4, N7], (N4), N4, [A4], [news 4], [T1], [Technical] or (T). normalise_citations() rewrites all of these to the
    canonical form before validation, so a formatting habit never costs an API call. A1 is accepted as N1 because some
    prompts in this project number articles A1..An, and models carry that habit over.
  - Truncated prices. Under strict JSON decoding, a model that writes "73,250" emits 73: the comma ends the number. A
    price below current/500 whose x1000 value lies inside the band is read as thousands. The prompt also prints every
    price without separators and says "write 73250.0", which prevents the habit in the first place.
  - Confidence above the cap. It is clamped to max_confidence (default 95) instead of rejected. The intent ("very sure")
    is clear, and the cap is our policy, not the model's mistake.

  REJECTED (substantive)
  a. Unknown citations. Headlines appear in the prompt as N1..Nk. Citing [N31] when we showed 30 means the model is
     pointing at a source that does not exist. That is the hallucination the closed-world design is there to catch, so it
     stays a hard failure.
  b. Missing grounding. sentiment_summary must cite at least one headline (when there is news), technical_summary must
     cite [T], and every reasoning must cite at least one fact of either kind. The prompt ASKS for both kinds in each
     reasoning; the validator only REQUIRES one, because a 14-day view can honestly rest on technicals alone.
  c. Volatility band. |ln(predicted / current)| <= k · σ_daily · √h, with σ measured from real prices (technicals.py).
     The default k is 4. A 4-sigma move happens roughly once in 16,000 draws under a normal random walk; BTC has fat
     tails, so real 4-sigma days happen more often than that, but a FORECAST (a central estimate, not a tail scenario)
     beyond it is almost certainly a made-up number. The exact allowed range per horizon is printed in the prompt, so the
     model never has to guess where the wall is.
  d. Financial advice. Reasoning that tells the reader to act is rejected. The detector targets the INSTRUCTION, not
     market vocabulary: "MicroStrategy continued to buy bitcoin", "leveraged longs were liquidated" and "whales
     accumulated" describe what happened and are allowed; "buy the dip", "you should", "set a stop-loss" are not.
  e. A forecast equal to today's price. The model must commit to a direction, at confidence 50 if it sees no edge.
  g. Confluence (when tech_signals is set). For each horizon the validator recomputes k, the number of the five
     confluence signals (confluence.py) that point in the forecast's direction, from the model's OWN output: the sign
     of predicted - current, and its sentiment_score for the news vote. Two checks: the reasoning must contain the
     breakdown line "Confluence: k/5" with the correct k, and the confidence must lie in the band for k
     (confluence.band). The band's floor drops to 50 when accuracy.regime_penalty() says the same regime has failed
     before. The repair message spells out every vote, so a miscount is fixed in one round trip.
  f. Missing advanced technicals. When MACD or Bollinger values were in the prompt (require_advanced), every reasoning
     must name MACD or the Bollinger Bands. The rubric's top tier depends on them, so a reasoning that never mentions
     them cannot have justified its confidence. A plain substring check: cheap, and hard to satisfy by accident.

WHAT "CONFIDENCE" MEANS (the same definition appears in the prompt): the probability, 0-100, that on the target date
the actual price will be on the same side of today's price as the forecast, i.e. that the forecast DIRECTION is right.
50 means a coin flip. A fixed definition makes the number measurable: after 14 days you can check whether "70%"
forecasts were right about 70% of the time.

Field ORDER matters. Models generate left to right, so the analysis fields (sentiment, technicals) come first and the
numbers come last: the model commits to its reading of the evidence before it writes a price.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.bitcoin import confluence
from app.bitcoin.technicals import HORIZONS

log = logging.getLogger(__name__)

NEWS_TAG = re.compile(r"\[(N\d{1,3})\]")
TECH_TAG = "[T]"
ADVANCED = re.compile(r"\bMACD\b|\bBollinger\b", re.I)
BREAKDOWN = re.compile(r"Confluence:\s*(\d)\s*/\s*5", re.I)
TEXT_FIELDS = ("sentiment_summary", "technical_summary", "reasoning_1d", "reasoning_3d", "reasoning_7d", "reasoning_14d")

# ------------------------------------------------------------------------------------------------ citation normalisation
# A bracketed or parenthesised group whose every token is a citation, e.g. "[N4, N7]", "(N4)", "[A2; T]", "[news 3]".
_GROUP = re.compile(r"[\[(]([^\[\]()\n]{1,80})[\])]")
_SPLIT = re.compile(r"\s*(?:,|;|/|&|\band\b)\s*", re.I)
_NEWS_TOKEN = re.compile(r"^(?:n|a|news|article|headline|source)\s*#?\s*-?\s*(\d{1,3})$", re.I)
_TECH_TOKEN = re.compile(r"^(?:t\d{0,2}|tech|technicals?|ta|indicators?|market[_ ]data)$", re.I)
# A citation outside brackets: "N4" or "A4" as a standalone word (case-sensitive, so "a4 paper" or "n1" text is untouched).
_BARE = re.compile(r"(?<![\w\[\-/.])[NA](\d{1,3})(?![\w\]%])")


def _canonical(token: str) -> str | None:
    token = token.strip()
    if m := _NEWS_TOKEN.match(token):
        return f"[N{int(m.group(1))}]"
    if _TECH_TOKEN.match(token):
        return TECH_TAG
    return None


def normalise_citations(text: str, valid_ids: frozenset[str] | None = None) -> str:
    """Rewrite common citation variants to the canonical [N#] / [T] form. Groups that are not purely citations, such as
    "(SMA-30)" or "(RSI 34)", are left alone. A bare word like "N4" becomes "[N4]" only if N4 is in `valid_ids` (when
    given), so ordinary text that happens to look like an id ("N95 masks") is never turned into a citation."""

    def group(m: re.Match) -> str:
        tokens = [t for t in _SPLIT.split(m.group(1)) if t.strip()]
        canon = [_canonical(t) for t in tokens]
        if not tokens or any(c is None for c in canon):
            return m.group(0)
        return "".join(dict.fromkeys(canon))  # dedupe while keeping order: "[N4, N4]" -> "[N4]"

    text = _GROUP.sub(group, text)

    def bare(m: re.Match) -> str:
        tag = f"N{int(m.group(1))}"
        return f"[{tag}]" if valid_ids is None or tag in valid_ids else m.group(0)

    return _BARE.sub(bare, text)


# ------------------------------------------------------------------------------------------------ advice detection
# Each alternative matches an INSTRUCTION to the reader, never a description of what market participants did.
ADVICE = re.compile(
    # addressing the reader or recommending
    r"\b(?:you|investors|traders|readers|holders)\s+(?:should|must|ought to|need to|may want to|might want to)\b"
    r"|\b(?:we|i)\s+(?:recommend|suggest|advise|would (?:buy|sell|short))\b"
    r"|\b(?:recommend(?:ed)?|consider)\s+(?:buying|selling|shorting|accumulating|holding)\b"
    # imperatives at the start of a sentence: "Buy the dip.", "Sell bitcoin now", "Hold your BTC"
    r"|(?:^|[.!?;:]\s+)(?:buy|sell|short|accumulate|hold)\s+(?:btc|bitcoin|now|here|the|some|more|into|on|your)\b"
    r"|\b(?:buy|sell|short)\s+(?:now|today|here|the dip|the rip|the bounce)\b"
    r"|\bgo\s+(?:long|short)\b"
    # ratings, trade management, sizing
    r"|\b(?:strong\s+)?(?:buy|sell)\s+(?:rating|opportunity|zone|recommendation)\b|\bstrong\s+(?:buy|sell)\b"
    r"|\b(?:set|place|use|move|keep)\s+(?:a\s+|your\s+)?(?:tight\s+)?(?:stop[- ]loss|take[- ]profit)"
    r"|\b(?:use|add|apply|avoid)\s+(?:\d+x\s+)?leverage\b"
    r"|\bposition[- ]siz(?:e|ing)\b"
    r"|\b(?:good|great|ideal|attractive)\s+(?:time|entry|moment|opportunity|level)\s+to\b|\b(?:good|attractive)\s+entry\b",
    re.I,
)


@dataclass(frozen=True)
class BitcoinGrounding:
    """Facts the validators check against. Passed to model_validate(..., context=...)."""

    news_ids: frozenset[str]  # "N1".."Nk" shown in the prompt
    current_price: float
    daily_sigma: float
    band_sigmas: float = 4.0
    max_confidence: int = 95
    require_advanced: bool = False  # True when MACD/Bollinger values were in the prompt (enough history to compute them)
    tech_signals: dict | None = None  # confluence.technical_signals() of today's indicators; None = rule not enforced
    accuracy: dict | None = None  # accuracy.get_past_accuracy(), for the regime-matched penalty


def band(current_price: float, daily_sigma: float, days: int, k: float) -> tuple[float, float]:
    """The allowed forecast range for a horizon: today's price ± k·σ·√days in log space. Shared by the validator and the
    prompt, so the model is told exactly the range it is checked against."""
    limit = k * daily_sigma * math.sqrt(days)
    return current_price * math.exp(-limit), current_price * math.exp(limit)


def _g(info: ValidationInfo) -> BitcoinGrounding | None:
    return info.context if isinstance(info.context, BitcoinGrounding) else None


def _check_text(v: str, info: ValidationInfo, *, need_news: bool = False, need_tech: bool = False, need_any: bool = False,
                need_advanced: bool = False) -> str:
    if m := ADVICE.search(v):
        raise ValueError(f"contains investment advice ({m.group(0).strip()!r}). Describe what the market is doing; never tell "
                         "the reader what to do")
    g = _g(info)
    if g is None:
        return v
    unknown = sorted({m for m in NEWS_TAG.findall(v) if m not in g.news_ids}, key=lambda s: int(s[1:]))
    if unknown:
        valid = f"N1..N{len(g.news_ids)}" if g.news_ids else "none (there is no news)"
        raise ValueError(f"cites news ids that do not exist: {unknown}. Valid ids are {valid}")
    has_news, has_tech = bool(NEWS_TAG.search(v)), TECH_TAG in v
    if need_news and g.news_ids and not has_news:
        raise ValueError("must cite at least one headline in the exact form [N#], e.g. [N3]")
    if need_tech and not has_tech:
        raise ValueError("must cite the indicators with the exact tag [T]")
    if need_any and not (has_news or has_tech):
        raise ValueError("must cite at least one fact: a headline as [N#] (e.g. [N3]) and/or an indicator as [T]")
    if need_advanced and g.require_advanced and not ADVANCED.search(v):
        raise ValueError("must state the MACD and/or Bollinger Band status with its value, e.g. \"MACD -957 sits below its "
                         "signal line -579 [T]\" or \"price is at %B 0.39 inside the Bollinger Bands [T]\"")
    return v


# The prompt asks for 2-3 paragraphs (~600-1800 chars). The cap is 2400 so a thorough note is never bounced for length;
# the floor stays low because depth is the prompt's job, and a length repair round-trip costs a full API call.
Reasoning = Field(min_length=40, max_length=2400, description="3 paragraphs separated by a blank line: macro and news (cite [N#]), technical setup (MACD, RSI and Bollinger values, cite [T]), then the prediction, risk and confidence justification.")
Confidence = Field(ge=0, le=100, description="Probability (0-100) that the price on the target date is on the SAME SIDE of today's price as your forecast. 50 = coin flip.")
Price = Field(gt=0, description="Forecast BTC price in USD on the target date. Must lie inside the plausible range given in <market_data>.")


class BitcoinPredictionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # --- 1. analysis first -------------------------------------------------------------------------------
    sentiment_summary: str = Field(min_length=40, max_length=1200, description="What the news collectively implies for BTC, citing headlines as [N#].")
    sentiment_score: int = Field(ge=-100, le=100, description="-100 very bearish .. 0 neutral .. +100 very bullish, from the news only.")
    technical_summary: str = Field(min_length=40, max_length=1200, description="What the indicators in <market_data> say, citing them as [T].")

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

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any, info: ValidationInfo) -> Any:
        """Cosmetic fixes before any rule runs: canonical citation tags, and confidence clamped to the cap."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        g = _g(info)
        for f in TEXT_FIELDS:
            if isinstance(data.get(f), str):
                data[f] = normalise_citations(data[f], g.news_ids if g else None).strip()
        if g is not None:
            for key, days in HORIZONS:
                # Thousands-separator truncation: a model that "writes 73,250" under strict JSON decoding emits 73, because
                # the comma ends the number. If x1000 lands inside the band, that is unambiguously what it meant.
                p = data.get(f"predicted_price_{key}")
                if isinstance(p, (int, float)) and 0 < p < g.current_price / 500:
                    lo, hi = band(g.current_price, g.daily_sigma, days, g.band_sigmas)
                    if lo <= p * 1000 <= hi:
                        log.warning("btc forecast: predicted_price_%s=%s read as %s (thousands separator truncated the number)", key, p, p * 1000)
                        data[f"predicted_price_{key}"] = p * 1000
                c = data.get(f"confidence_{key}")
                if isinstance(c, (int, float)) and c > g.max_confidence:
                    log.warning("btc forecast: confidence_%s=%s clamped to the cap %d", key, c, g.max_confidence)
                    data[f"confidence_{key}"] = g.max_confidence
        return data

    @field_validator("sentiment_summary")
    @classmethod
    def _sentiment(cls, v: str, info: ValidationInfo) -> str:
        return _check_text(v, info, need_news=True)

    @field_validator("technical_summary")
    @classmethod
    def _technical(cls, v: str, info: ValidationInfo) -> str:
        return _check_text(v, info, need_tech=True)

    @field_validator("reasoning_1d", "reasoning_3d", "reasoning_7d", "reasoning_14d")
    @classmethod
    def _reasoning(cls, v: str, info: ValidationInfo) -> str:
        return _check_text(v, info, need_any=True, need_advanced=True)

    @model_validator(mode="after")
    def _numbers(self, info: ValidationInfo) -> "BitcoinPredictionOutput":
        g = _g(info)
        if g is None:
            return self
        problems = []
        for key, days in HORIZONS:
            price = getattr(self, f"predicted_price_{key}")
            lo, hi = band(g.current_price, g.daily_sigma, days, g.band_sigmas)
            if not lo <= price <= hi:
                move = (price / g.current_price - 1) * 100
                problems.append(f"predicted_price_{key}={price:.0f} is a {move:+.1f}% move, outside the plausible range "
                                f"{lo:.0f}..{hi:.0f} for {days} day(s); pick a price inside that range")
            if abs(price - g.current_price) < 0.01:
                problems.append(f"predicted_price_{key} equals today's price: commit to a direction (confidence 50 if you see no edge)")
            elif g.tech_signals is not None:
                problems += self._confluence_problems(g, key, days, 1 if price > g.current_price else -1)
        if problems:
            raise ValueError("; ".join(problems))
        confs = {getattr(self, f"confidence_{key}") for key, _ in HORIZONS}
        if len(confs) == 1:  # allowed (the prompt permits it with a reason) but it is the failure mode the rubric targets
            log.warning("btc forecast: identical confidence %d%% on every horizon", confs.pop())
        return self

    def _confluence_problems(self, g: BitcoinGrounding, key: str, days: int, direction: int) -> list[str]:
        from app.bitcoin.accuracy import regime_penalty  # local import: accuracy imports the DB layer

        k, votes = confluence.confluence(g.tech_signals, self.sentiment_score, direction)
        lo, hi = confluence.band(k, key)
        penalty = regime_penalty(g.accuracy, key, days, k)
        if penalty:
            lo = 50
        why = f"a {'UP' if direction > 0 else 'DOWN'} forecast has confluence {k}/5 ({confluence.describe(votes)})"
        out = []
        conf = getattr(self, f"confidence_{key}")
        if not lo <= conf <= hi:
            out.append(f"confidence_{key}={conf} but {why}, so confidence must be {lo}-{hi}"
                       + (f" (floor lowered to 50 by the {penalty})" if penalty else ""))
        written = BREAKDOWN.findall(getattr(self, f"reasoning_{key}"))
        if not written:
            out.append(f"reasoning_{key} must end with the breakdown line, e.g. \"Confluence: {k}/5 ({confluence.describe(votes)}). "
                       f"Confidence set to {min(max(conf, lo), hi)}%.\"")
        elif int(written[-1]) != k:
            out.append(f"reasoning_{key} says Confluence: {written[-1]}/5 but {why}")
        return out

    def horizon(self, key: str) -> tuple[float, int, str]:
        return getattr(self, f"predicted_price_{key}"), getattr(self, f"confidence_{key}"), getattr(self, f"reasoning_{key}")
