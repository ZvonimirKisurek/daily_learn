"""OpenAI client wrapper: schema-constrained generation + Pydantic validation + repair loop.

Control flow of `StructuredLLM.generate`:

    prompt ──► OpenAI chat completion (response_format = strict JSON Schema) ──► Pydantic validate (+ grounding context)
                    ▲                                                                 │ ok  ──► return typed object
                    │                                                                 │ fail
                    └──── append the model's answer and the exact validation errors ◄─┘  (max N times)

OPENAI STRUCTURED OUTPUTS, IN ONE PARAGRAPH. With `response_format={"type": "json_schema", "json_schema":
{"name": ..., "strict": True, "schema": ...}}` the server compiles the schema into a grammar and, while the model
generates, masks every token that would violate it (constrained decoding). Output therefore always parses and has
every field with the right type. It does NOT enforce *values*: strict mode supports only a subset of JSON Schema, and
"this id must be one we showed you" cannot be expressed in a schema at all. That is the job of the Pydantic
validators in schemas.py, run here with `context=grounding`; a failure triggers the repair loop below.

Why a repair loop? Showing the model its own output plus a precise error list is cheap and almost always fixes the
problem on the first retry, far more reliable than hoping a stricter prompt prevents every mistake.

Reliability layers: the SDK retries 429/5xx/connection errors with exponential backoff (llm_sdk_retries); refusals,
truncation and an exhausted account are typed errors, never silently parsed; the spend cap is checked before every
attempt; Celery adds an outer retry for transient failures.

This client is synchronous on purpose: every caller (Celery tasks, the CLI, the inline job thread) is synchronous and
calls are sequential by design, so an async client would only add event-loop plumbing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.curriculum.errors import LLMBudgetExceeded, LLMError, LLMRefusal, LLMTruncated, LLMValidationFailed  # noqa: F401 (re-exported)
from app.curriculum.usage import DbUsage

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# JSON-Schema keywords that strict mode may or may not accept depending on model and date. We never send them:
# they are folded into the field description (so the model still sees the limit) and Pydantic enforces them.
_CONSTRAINT_KEYS = ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "pattern")


def _hint(node: dict[str, Any]) -> str:
    parts = []
    if "minLength" in node or "maxLength" in node:
        parts.append(f"{node.get('minLength', 0)}-{node['maxLength']} characters" if "maxLength" in node else f"at least {node['minLength']} characters")
    if "minItems" in node or "maxItems" in node:
        lo, hi = node.get("minItems"), node.get("maxItems")
        parts.append(f"exactly {lo} items" if lo is not None and lo == hi else f"{lo or 0}-{hi} items" if hi is not None else f"at least {lo} items")
    if "minimum" in node or "maximum" in node:
        parts.append(f"between {node.get('minimum', '-inf')} and {node.get('maximum', 'inf')}")
    return "; ".join(parts)


def _relax(node: Any) -> Any:
    if isinstance(node, list):
        return [_relax(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        # `properties`/`$defs` map NAMES to schemas: recurse into the values, never treat the map itself as a schema.
        out[key] = {k: _relax(v) for k, v in value.items()} if key in ("properties", "$defs") else _relax(value)
    hint = _hint(out)
    for k in _CONSTRAINT_KEYS:
        out.pop(k, None)
    if hint:
        out["description"] = f"{out['description']} [{hint}]" if out.get("description") else f"[{hint}]"
    return out


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model -> JSON Schema that OpenAI strict mode accepts.

    `openai.pydantic_function_tool` (public SDK helper) does the strict-mode rewrite: additionalProperties=false on
    every object, every property listed in `required` (optional fields become nullable), $refs resolved.
    `_relax` then removes value constraints; Pydantic keeps enforcing them after generation.
    """
    return _relax(openai.pydantic_function_tool(model, name=model.__name__)["function"]["parameters"])


def format_errors(exc: ValidationError) -> str:
    # Locations + messages only. Echoing input values could replay a large or injected payload back to the model.
    return "\n".join(f"- {'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())


class StructuredLLM:
    def __init__(self, settings: Settings, client: openai.OpenAI | None = None, usage=None) -> None:
        self._s = settings
        self._usage = usage or DbUsage(settings)
        self._client = client or openai.OpenAI(api_key=settings.openai_api_key or None, max_retries=settings.llm_sdk_retries,
                                                 timeout=settings.llm_timeout_seconds)  # a stalled request is retried, not waited on for 10 min

    # ------------------------------------------------------------------ one chat-completion attempt (shared by both modes)
    def _chat(self, *, purpose: str, model: str, messages: list[dict], response_format: dict | None,
              temperature: float | None, max_tokens: int, attempt: int):
        s = self._s
        self._usage.check()  # hard daily cap: refuse BEFORE spending, on every attempt including repairs
        kwargs: dict[str, Any] = dict(model=model, messages=messages, max_completion_tokens=max_tokens)
        if response_format is not None:
            kwargs["response_format"] = response_format
        if temperature is not None:
            kwargs["temperature"] = temperature  # sampling temperature: see the explanation in prompts.py
        if s.llm_seed is not None:
            kwargs["seed"] = s.llm_seed  # best-effort reproducibility
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            if getattr(exc, "code", None) == "insufficient_quota":  # out of credits is not transient: do not retry
                raise LLMBudgetExceeded("OpenAI reports insufficient quota: the account's credit/budget limit was reached") from exc
            raise
        choice = resp.choices[0]
        if resp.usage:
            self._usage.record(purpose, model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        log.info("llm %s attempt=%d model=%s finish=%s tokens=%s", purpose, attempt, model, choice.finish_reason, resp.usage and resp.usage.total_tokens)
        if choice.message.refusal or choice.finish_reason == "content_filter":
            raise LLMRefusal(f"model refused: {choice.message.refusal or 'content filter'}")
        if choice.finish_reason == "length":
            raise LLMTruncated(f"output hit max_completion_tokens={max_tokens}; raise LLM_MAX_TOKENS / LLM_CHAPTER_MAX_TOKENS")
        return choice.message.content or ""

    # ------------------------------------------------------------------ mode 1: structured JSON (strict schema + Pydantic + repair)
    def generate(self, *, system: str, user: str, output_model: type[T], grounding: object,
                 model: str | None = None, temperature: float | None = None) -> T:
        s = self._s
        model = model or s.llm_model
        response_format = {"type": "json_schema", "json_schema": {"name": output_model.__name__, "strict": True, "schema": strict_schema(output_model)}}
        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_errors = ""
        raw_outputs: list[str] = []
        for attempt in range(s.llm_max_repairs + 1):
            text = self._chat(purpose=output_model.__name__, model=model, messages=messages, response_format=response_format,
                              temperature=temperature, max_tokens=s.llm_max_tokens, attempt=attempt)
            try:
                return output_model.model_validate_json(text, context=grounding)
            except ValidationError as exc:
                last_errors = format_errors(exc)
                raw_outputs.append(text)
                # The raw reply is model output (no keys, no secrets), so it is safe to log in full, and it is the only
                # way to see WHY a rule keeps failing. Truncated so one bad reply cannot flood the log.
                log.error("%s failed validation (attempt %d/%d):\n%s\n----- raw model output -----\n%s\n----- end raw output -----",
                          output_model.__name__, attempt + 1, s.llm_max_repairs + 1, last_errors, text[:12_000])
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Your JSON failed validation:\n" + last_errors + "\nReturn the complete corrected JSON object."},
                ]
        err = LLMValidationFailed(f"still invalid after {s.llm_max_repairs} repairs:\n{last_errors}")
        err.errors, err.raw_outputs = last_errors, raw_outputs  # for callers that want to log or surface the details
        raise err

    # ------------------------------------------------------------------ mode 2: long-form markdown, validated by measurement
    def generate_text(self, *, system: str, user: str, check: Callable[[str], list[str]], purpose: str,
                      model: str | None = None, temperature: float | None = None, max_tokens: int | None = None,
                      clean: Callable[[str], str] | None = None) -> str:
        """Free-form text (a chapter, an essay) that must satisfy `check`, a function returning a list of problems.

        Why not put a 2,000-word chapter inside a JSON string? JSON-escaped markdown (\\n, \\", code fences) degrades
        quality and truncates easily, and a JSON schema cannot express "at least 1,500 words". Plain text lets the model
        write naturally; `check` measures what we required (words, structure, valid Python, citations, no links) and the same
        repair idea applies: show the model its own text plus the exact problems and ask for the complete corrected text.

        `clean` (optional) deterministically fixes things not worth a repair round-trip (e.g. a stray `# Title`). It runs
        on every reply BEFORE `check`, and its output is what is checked, shown back to the model, and returned/saved.
        """
        s = self._s
        model = model or s.llm_model
        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        problems: list[str] = []
        for attempt in range(s.llm_max_repairs + 1):
            text = self._chat(purpose=purpose, model=model, messages=messages, response_format=None, temperature=temperature,
                              max_tokens=max_tokens or s.llm_chapter_max_tokens, attempt=attempt)
            if clean:
                text = clean(text)
            problems = check(text)
            if not problems:
                return text.strip()
            log.warning("%s failed checks (attempt %d): %s", purpose, attempt, problems)
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Your text does not meet the requirements:\n" + "\n".join(f"- {p}" for p in problems)
                 + "\nReturn the COMPLETE corrected text (not just the changed parts), in the same markdown format, with no preamble."},
            ]
        raise LLMValidationFailed(f"{purpose} still failing after {s.llm_max_repairs} repairs:\n" + "\n".join(problems))


def make_llm(settings: Settings):
    """The configured text-generation backend (LLM_PROVIDER). Both expose generate() and generate_text()."""
    if settings.llm_provider == "claude_code":
        from app.curriculum.claude_code import ClaudeCodeLLM

        return ClaudeCodeLLM(settings)
    if settings.llm_provider != "openai":
        raise ValueError(f"LLM_PROVIDER must be 'openai' or 'claude_code', got {settings.llm_provider!r}")
    return StructuredLLM(settings)
