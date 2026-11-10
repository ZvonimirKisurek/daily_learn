"""An LLM backend that runs on a Claude SUBSCRIPTION instead of a pay-per-token API key.

How: it shells out to the official, unmodified Claude Code program in headless mode (`claude -p`), signed in with the
owner's own Claude account. On a server there is no browser, so the sign-in is a one-year token made once with
`claude setup-token` and passed as CLAUDE_CODE_OAUTH_TOKEN. Usage then counts against the plan's limits, not a bill.

    system prompt ──► --system-prompt-file        user prompt ──► stdin
    JSON Schema  ──► --json-schema  ──► the reply's `structured_output`  ──► Pydantic validation ──► repair loop (ours)

Same contract as StructuredLLM (generate / generate_text), so the generators, the grader and the Bitcoin forecaster do
not know which backend they are talking to. What changes:
  * No tools, no project files, no MCP servers, no saved sessions: the model only sees the two prompts we send.
  * No sampling temperature (Claude Code does not expose one); the `temperature` argument is accepted and ignored.
  * Each call is a fresh process, so a repair attempt re-sends the conversation as text instead of as chat turns.
  * No dollar cap: nothing is billed per call. The plan's own usage limit is the cap, reported as LLMBudgetExceeded.
This is for a single-owner site. It must not be used to serve other people from one person's subscription.
EMBEDDINGS are not available on Claude at all: de-duplication and ranking still use the embeddings API.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.curriculum.errors import LLMBudgetExceeded, LLMError, LLMRefusal, LLMValidationFailed

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_LIMIT_HINTS = ("usage limit", "rate limit", "limit reached", "quota")
_AUTH_HINTS = ("invalid api key", "authentication", "unauthorized", "/login", "oauth", "not logged in")


class ClaudeCodeLLM:
    def __init__(self, settings: Settings, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self._s = settings
        self._run = runner or subprocess.run  # injectable so tests never start a real process

    # ------------------------------------------------------------------ one headless run
    def _call(self, *, purpose: str, system: str, prompt: str, schema: dict | None, model: str | None, attempt: int) -> dict:
        s = self._s
        with tempfile.TemporaryDirectory(prefix="claude-llm-") as tmp:
            sys_path = os.path.join(tmp, "system.txt")
            with open(sys_path, "w", encoding="utf-8") as f:
                f.write(system)
            cmd = [s.claude_code_bin, "-p", "--output-format", "json", "--system-prompt-file", sys_path,
                   "--tools", "", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence",
                   "--setting-sources", ""]
            if model or s.claude_code_model:
                cmd += ["--model", model or s.claude_code_model]
            if s.claude_code_effort:
                cmd += ["--effort", s.claude_code_effort]
            if schema is not None:
                cmd += ["--json-schema", json.dumps(schema)]
            try:
                # cwd = the empty temp dir: nothing for the agent to read, even though it has no tools anyway
                proc = self._run(cmd, input=prompt, capture_output=True, text=True, timeout=s.claude_code_timeout_seconds, cwd=tmp)
            except FileNotFoundError:
                raise LLMError(f"Claude Code is not installed ({s.claude_code_bin!r} not found). Rebuild the backend image, or set LLM_PROVIDER=openai.") from None
            except subprocess.TimeoutExpired:
                raise LLMError(f"Claude Code did not answer within {s.claude_code_timeout_seconds:.0f}s ({purpose})") from None
        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            data = None
        if not isinstance(data, dict) or proc.returncode != 0 or data.get("is_error"):
            detail = (data or {}).get("result") if isinstance(data, dict) else None
            detail = str(detail or proc.stderr or proc.stdout or "no output").strip()[:600]
            low = detail.lower()
            if (isinstance(data, dict) and data.get("api_error_status") == 429) or any(h in low for h in _LIMIT_HINTS):
                raise LLMBudgetExceeded(f"Claude usage limit reached: {detail}")
            if any(h in low for h in _AUTH_HINTS):
                raise LLMError("Claude Code is not signed in. Run `claude setup-token` on your own computer and put the token in "
                               f".env as CLAUDE_CODE_OAUTH_TOKEN. ({detail})")
            raise LLMError(f"Claude Code failed ({purpose}, exit {proc.returncode}): {detail}")
        usage = data.get("usage") or {}
        log.info("llm %s attempt=%d backend=claude_code model=%s turns=%s tokens_out=%s", purpose, attempt,
                 ",".join((data.get("modelUsage") or {}).keys()) or "default", data.get("num_turns"), usage.get("output_tokens"))
        return data

    @staticmethod
    def _with_history(user: str, history: list[tuple[str, str]]) -> str:
        """A repair attempt as ONE prompt: the task, then each earlier answer with the problems found in it."""
        if not history:
            return user
        parts = [user]
        for answer, feedback in history:
            parts.append(f"\n\n<your_previous_answer>\n{answer}\n</your_previous_answer>\n\n{feedback}")
        return "".join(parts)

    # ------------------------------------------------------------------ mode 1: structured JSON
    def generate(self, *, system: str, user: str, output_model: type[T], grounding: object,
                 model: str | None = None, temperature: float | None = None) -> T:
        from app.curriculum.llm import format_errors, strict_schema

        s = self._s
        schema = strict_schema(output_model)
        history: list[tuple[str, str]] = []
        last_errors, raw_outputs = "", []
        for attempt in range(s.llm_max_repairs + 1):
            data = self._call(purpose=output_model.__name__, system=system, prompt=self._with_history(user, history), schema=schema,
                              model=model, attempt=attempt)
            if "structured_output" not in data:
                raise LLMRefusal(f"Claude returned no structured output: {str(data.get('result') or '')[:300]}")
            text = json.dumps(data["structured_output"])
            try:
                return output_model.model_validate_json(text, context=grounding)
            except ValidationError as exc:
                last_errors = format_errors(exc)
                raw_outputs.append(text)
                log.error("%s failed validation (attempt %d/%d):\n%s\n----- raw model output -----\n%s\n----- end raw output -----",
                          output_model.__name__, attempt + 1, s.llm_max_repairs + 1, last_errors, text[:12_000])
                history.append((text, "Your JSON failed validation:\n" + last_errors + "\nReturn the complete corrected JSON object."))
        err = LLMValidationFailed(f"still invalid after {s.llm_max_repairs} repairs:\n{last_errors}")
        err.errors, err.raw_outputs = last_errors, raw_outputs
        raise err

    # ------------------------------------------------------------------ mode 2: long-form markdown
    def generate_text(self, *, system: str, user: str, check: Callable[[str], list[str]], purpose: str,
                      model: str | None = None, temperature: float | None = None, max_tokens: int | None = None,
                      clean: Callable[[str], str] | None = None) -> str:
        s = self._s
        history: list[tuple[str, str]] = []
        problems: list[str] = []
        for attempt in range(s.llm_max_repairs + 1):
            data = self._call(purpose=purpose, system=system, prompt=self._with_history(user, history), schema=None, model=model, attempt=attempt)
            text = str(data.get("result") or "")
            if clean:
                text = clean(text)
            problems = check(text)
            if not problems:
                return text.strip()
            log.warning("%s failed checks (attempt %d): %s", purpose, attempt, problems)
            history.append((text, "Your text does not meet the requirements:\n" + "\n".join(f"- {p}" for p in problems)
                            + "\nReturn the COMPLETE corrected text (not just the changed parts), in the same markdown format, with no preamble."))
        raise LLMValidationFailed(f"{purpose} still failing after {s.llm_max_repairs} repairs:\n" + "\n".join(problems))
