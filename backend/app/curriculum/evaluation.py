"""Grading a hands-on submission with the LLM, against a rubric fixed when the task was created.

Trust model: the SUBMISSION is untrusted input (it may try to talk the grader into a pass), and the GRADER is not trusted to
do arithmetic or to stay consistent. So: the submission is escaped and fenced (prompts.build_evaluation_prompt); the
grader returns per-criterion points; `schemas.TaskEvaluationSchema` re-computes score and pass/fail from those points with
the rubric (compute_grade) and rejects any answer that disagrees, which sends the grader through the repair loop. The skill
matrix moves only if the VERIFIED `passed` is true.

Honest limit: this is an AI code review plus a consistency check of the pasted output, not execution. It can be wrong in
both directions; the rubric asks for output evidence and the feedback explains its reasoning so you can disagree.
"""
from __future__ import annotations

from app.config import Settings
from app.curriculum import prompts
from app.curriculum.llm import StructuredLLM
from app.curriculum.schemas import GroundingContext, Rubric, TaskEvaluationSchema


def evaluate(llm: StructuredLLM, settings: Settings, *, task: dict, rubric: dict, submission: str,
             tests: str | None = None) -> TaskEvaluationSchema:
    """`tests` is a one-line description of the parsed acceptance-test report; the grader sees it as evidence, and
    app.mastery.acceptance.apply_tests adds the code-scored test criterion afterwards."""
    rubric_model = Rubric.model_validate(rubric)
    return llm.generate(
        system=prompts.EVALUATOR_SYSTEM,
        user=prompts.build_evaluation_prompt(task, rubric, submission, settings.max_submission_chars, tests),
        output_model=TaskEvaluationSchema,
        grounding=GroundingContext(frozenset(), frozenset(), None, rubric_model),
        temperature=settings.temp_eval,
    )
