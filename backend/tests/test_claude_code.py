"""The Claude-subscription backend (curriculum/claude_code.py), with a fake process: no network, no real `claude`."""
import json
import subprocess

import pytest

from app.config import Settings
from app.curriculum.claude_code import ClaudeCodeLLM
from app.curriculum.errors import LLMBudgetExceeded, LLMError, LLMRefusal, LLMValidationFailed
from app.curriculum.llm import StructuredLLM, make_llm
from app.curriculum.schemas import DailyPlanOutput, GroundingContext
from tests.factories import daily_dict

CTX = GroundingContext(frozenset({"A1"}), frozenset({"rag", "vector-db"}))


class Runner:
    """Stands in for subprocess.run: replays canned stdout payloads and records what was asked."""
    def __init__(self, *replies, returncode=0, stderr=""):
        self.replies, self.calls, self.returncode, self.stderr = list(replies), [], returncode, stderr

    def __call__(self, cmd, *, input, capture_output, text, timeout, cwd):
        system = open(cmd[cmd.index("--system-prompt-file") + 1]).read()
        self.calls.append({"cmd": cmd, "prompt": input, "system": system, "timeout": timeout})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=reply if isinstance(reply, str) else json.dumps(reply), stderr=self.stderr)


def ok(**kw):
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "usage": {"output_tokens": 10}, **kw}


def llm(runner, **settings):
    return ClaudeCodeLLM(Settings(llm_provider="claude_code", **settings), runner=runner)


def test_structured_call_is_isolated_and_validated():
    r = Runner(ok(structured_output=daily_dict("A1")))
    out = llm(r, claude_code_model="sonnet").generate(system="SYS", user="USER", output_model=DailyPlanOutput, grounding=CTX, temperature=0.3)
    assert out.reading.chapter_title and len(r.calls) == 1
    c = r.calls[0]
    assert c["system"] == "SYS" and c["prompt"] == "USER"                       # prompts travel by file and stdin, not argv
    cmd = c["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--tools") + 1] == ""  # no tools: text in, text out
    assert {"--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"} <= set(cmd) and "--bare" not in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet" and json.loads(cmd[cmd.index("--json-schema") + 1])["type"] == "object"


def test_validation_failure_is_repaired_by_resending_the_answer_with_its_errors():
    r = Runner(ok(structured_output=daily_dict("A99")), ok(structured_output=daily_dict("A1")))
    llm(r).generate(system="S", user="TASK", output_model=DailyPlanOutput, grounding=CTX)
    second = r.calls[1]["prompt"]
    assert second.startswith("TASK") and "<your_previous_answer>" in second and "not one of the provided article ids" in second
    bad = Runner(*[ok(structured_output=daily_dict("A99"))] * 2)
    with pytest.raises(LLMValidationFailed) as e:
        llm(bad, llm_max_repairs=1).generate(system="S", user="U", output_model=DailyPlanOutput, grounding=CTX)
    assert len(bad.calls) == 2 and len(e.value.raw_outputs) == 2


def test_text_mode_checks_cleans_and_repairs():
    r = Runner(ok(result="# Title\nshort"), ok(result="long enough text here"))
    out = llm(r).generate_text(system="S", user="U", purpose="Chapter", clean=lambda t: t.replace("# Title\n", ""),
                               check=lambda t: [] if len(t.split()) >= 4 else ["too short"])
    assert out == "long enough text here" and "too short" in r.calls[1]["prompt"] and "--json-schema" not in r.calls[0]["cmd"]


@pytest.mark.parametrize("runner,exc,needle", [
    (Runner({"is_error": True, "result": "Claude usage limit reached. Your limit will reset at 6pm."}, returncode=1), LLMBudgetExceeded, "usage limit"),
    (Runner({"is_error": True, "api_error_status": 429, "result": "Too many requests"}, returncode=1), LLMBudgetExceeded, "Too many"),
    (Runner({"is_error": True, "result": "Invalid API key · Please run /login"}, returncode=1), LLMError, "setup-token"),
    (Runner("not json at all", returncode=1, stderr="boom"), LLMError, "boom"),
    (Runner(FileNotFoundError()), LLMError, "not installed"),
    (Runner(subprocess.TimeoutExpired("claude", 600)), LLMError, "did not answer"),
    (Runner(ok(result="I can't help with that")), LLMRefusal, "no structured output"),
])
def test_failures_become_typed_errors(runner, exc, needle):
    with pytest.raises(exc, match=needle):
        llm(runner).generate(system="S", user="U", output_model=DailyPlanOutput, grounding=CTX)


def test_provider_switch():
    assert isinstance(make_llm(Settings(llm_provider="claude_code")), ClaudeCodeLLM)
    assert isinstance(make_llm(Settings(llm_provider="openai", openai_api_key="sk-test")), StructuredLLM)
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        make_llm(Settings(llm_provider="gemini"))
