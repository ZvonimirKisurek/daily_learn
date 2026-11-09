"""StructuredLLM against the OpenAI SDK (mocked HTTP): request shape, strict schemas, validation, repair loop, errors."""
import json

import pytest

from app.curriculum.errors import LLMBudgetExceeded, LLMRefusal, LLMTruncated, LLMValidationFailed
from app.curriculum.llm import strict_schema
from app.curriculum.schemas import DailyPlanOutput, GroundingContext, SummaryBatch, TaskEvaluationSchema, WeeklyThemeOutput
from app.curriculum.usage import NullUsage
from tests.factories import daily_dict
from tests.llm_helpers import completion, make_llm

CTX = GroundingContext(frozenset({"A1", "A2", "A3", "A4"}), frozenset({"rag", "vector-db", "pytorch"}))
GOOD = json.dumps(daily_dict("A1"))
BAD = json.dumps(daily_dict("A99"))


def gen(llm, **kw):
    return llm.generate(system="SYS", user="USR", output_model=DailyPlanOutput, grounding=CTX, **kw)


# ------------------------------------------------------------------ strict-mode schema
def walk(node, path="root"):
    problems = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            if set(node.get("properties", {})) != set(node.get("required", [])):
                problems.append(f"{path}: every property must be in required")
        problems += [f"{path}: unsupported keyword {k}" for k in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum") if k in node]
        for k, v in node.items():
            problems += walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            problems += walk(v, f"{path}[{i}]")
    return problems


@pytest.mark.parametrize("model", [WeeklyThemeOutput, DailyPlanOutput, TaskEvaluationSchema, SummaryBatch])
def test_every_schema_satisfies_openai_strict_mode_rules(model):
    assert walk(strict_schema(model)) == []


def test_limits_move_into_descriptions_and_nullable_fields_stay_required():
    d = strict_schema(DailyPlanOutput)["$defs"]["HandsOnTask"]["properties"]
    assert "2-10 items" in d["steps"]["description"] and "100 characters" in d["title"]["description"]
    assert "stretch_goal" in strict_schema(DailyPlanOutput)["$defs"]["HandsOnTask"]["required"]  # optional => nullable, not omitted


# ------------------------------------------------------------------ request shape
def test_request_uses_strict_json_schema_chat_completion():
    sent = []
    llm, _ = make_llm([completion(GOOD)], captured=sent, usage=NullUsage())
    out = gen(llm, temperature=0.3)
    assert out.reading.inspiration_article_ids == ["A1"]
    req = sent[0]
    assert req["model"] == "gpt-4o" and req["temperature"] == 0.3 and req["max_completion_tokens"] == 8000
    assert [m["role"] for m in req["messages"]] == ["system", "user"] and req["messages"][0]["content"] == "SYS"
    rf = req["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["name"] == "DailyPlanOutput"
    assert rf["json_schema"]["schema"]["additionalProperties"] is False


def test_temperature_and_seed_are_optional_and_model_is_overridable():
    sent = []
    llm, _ = make_llm([completion(GOOD)], captured=sent, usage=NullUsage(), llm_seed=7)
    gen(llm, model="gpt-4o-mini")
    assert "temperature" not in sent[0] and sent[0]["seed"] == 7 and sent[0]["model"] == "gpt-4o-mini"


# ------------------------------------------------------------------ validation + repair loop
def test_repair_loop_recovers_and_sends_the_errors_back():
    sent = []
    llm, _ = make_llm([completion(BAD), completion(GOOD)], captured=sent, usage=NullUsage())
    assert gen(llm).reading.inspiration_article_ids == ["A1"]
    assert len(sent) == 2
    second = sent[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == BAD                                   # the model sees its own answer...
    assert "reading.inspiration_article_ids" in second[3]["content"]           # ...and exactly where it was wrong
    assert "not one of the provided article ids" in second[3]["content"]


def test_repair_loop_fixes_a_url_and_a_wrong_skill_in_one_round():
    d = daily_dict("A1")
    d["market_insight"]["thesis"] = "see https://x.test"
    d["hands_on_task"]["skill_slug"] = "cooking"
    sent = []
    llm, _ = make_llm([completion(json.dumps(d)), completion(GOOD)], captured=sent, usage=NullUsage())
    gen(llm)
    msg = sent[1]["messages"][-1]["content"]
    assert "do not include URLs" in msg and "not a known skill slug" in msg


def test_unparseable_text_is_also_repaired():
    llm, sent = make_llm([completion("not json at all"), completion(GOOD)], usage=NullUsage())
    assert gen(llm).hands_on_task.title == "Chunk a PDF" and len(sent) == 2


def test_gives_up_after_max_repairs_without_returning_bad_data():
    llm, sent = make_llm([completion(BAD)] * 3, usage=NullUsage(), llm_max_repairs=2)
    with pytest.raises(LLMValidationFailed, match="A99"):
        gen(llm)
    assert len(sent) == 3


# ------------------------------------------------------------------ typed failure paths
def test_refusal_truncation_and_content_filter_are_typed_errors():
    with pytest.raises(LLMRefusal, match="cannot help"):
        gen(make_llm([completion("", refusal="I cannot help with that")], usage=NullUsage())[0])
    with pytest.raises(LLMTruncated):
        gen(make_llm([completion("{", finish="length")], usage=NullUsage())[0])
    with pytest.raises(LLMRefusal):
        gen(make_llm([completion("", finish="content_filter")], usage=NullUsage())[0])


def test_insufficient_quota_is_not_retried_and_maps_to_budget_error():
    body = {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "param": None, "code": "insufficient_quota"}}
    llm, sent = make_llm([(429, body), completion(GOOD)], usage=NullUsage())
    with pytest.raises(LLMBudgetExceeded, match="insufficient quota"):
        gen(llm)
    assert len(sent) == 1


def test_plain_rate_limit_is_raised_for_the_outer_retry_layer():
    import openai
    llm, _ = make_llm([(429, {"error": {"message": "slow down", "type": "requests", "param": None, "code": "rate_limit_exceeded"}})], usage=NullUsage())
    with pytest.raises(openai.RateLimitError):
        gen(llm)


# ------------------------------------------------------------------ the migration is complete
def test_no_anthropic_references_remain_in_code_or_dependencies():
    from pathlib import Path
    root = Path(__file__).parents[1]
    for path in list((root / "app").rglob("*.py")) + list((root / "scripts").rglob("*.py")) + [root / "pyproject.toml"]:
        text = path.read_text().lower()
        assert "import anthropic" not in text and "from anthropic" not in text and "anthropic_api_key" not in text, path
    assert "anthropic" not in (root / "pyproject.toml").read_text().lower()


# ------------------------------------------------------------------ embeddings: out-of-credit is not retried
def test_embedding_retry_predicate_skips_insufficient_quota():
    import httpx2
    import openai
    from app.ingestion.embeddings import _is_transient

    def err(cls, status, code):
        resp = httpx2.Response(status, request=httpx2.Request("POST", "http://x"))
        return cls("boom", response=resp, body={"code": code, "message": "boom"})

    assert not _is_transient(err(openai.RateLimitError, 429, "insufficient_quota"))
    assert _is_transient(err(openai.RateLimitError, 429, "rate_limit_exceeded"))
    assert _is_transient(err(openai.InternalServerError, 500, None))
    assert not _is_transient(ValueError("x"))
