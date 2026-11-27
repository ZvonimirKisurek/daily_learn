"""Reading `pytest -v` output with code, so the test result - not an LLM's impression - decides the grade.

The learner runs the acceptance suite on their own machine (`python -m pytest -v test_acceptance.py`, or
`tools/mle_check.py` which does it for them) and submits the output. We parse each `file::test_name STATUS` line and
compare against the test names stored when the suite was generated:
  * a test that never appears in the output counts as not passed (stale or edited suite, partial paste),
  * only PASSED counts; FAILED, ERROR, SKIPPED, XFAIL all count as not passed.
This is a personal tool, so a learner *could* fake the output; the goal is honest feedback, not proctoring. What it
does prevent is the much more common failure: believing you are done because the code "looks right".
"""
from __future__ import annotations

import re

_LINE = re.compile(r"(?:^|\s)(?:[\w./\\-]+\.py)::(?P<name>[\w\[\]\-.,= ]+?)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
_SUMMARY = re.compile(r"=+ .*?(\d+ (?:passed|failed|error|errors|skipped)).*? in [\d.]+s", re.I)


def parse(output: str, expected: list[str]) -> dict:
    """{expected, passed, failed, missing, all_passed, results: {name: status}, summary_line}."""
    results: dict[str, str] = {}
    for m in _LINE.finditer(output or ""):
        name = m.group("name").split("[")[0].strip()  # parametrised ids collapse to the function name
        status = m.group("status")
        # a parametrised test passes only if every case passed
        if results.get(name) not in (None, "PASSED") and status == "PASSED":
            continue
        results[name] = status
    exp = list(dict.fromkeys(expected))
    passed = [n for n in exp if results.get(n) == "PASSED"]
    missing = [n for n in exp if n not in results]
    failed = [n for n in exp if n in results and results[n] != "PASSED"]
    summary = _SUMMARY.search(output or "")
    return {"expected": len(exp), "passed": len(passed), "failed": failed, "missing": missing,
            "all_passed": bool(exp) and len(passed) == len(exp), "results": {n: results.get(n, "MISSING") for n in exp},
            "summary_line": summary.group(0).strip("= ") if summary else None}


def describe(report: dict) -> str:
    """One line for the grader prompt and the evaluation evidence."""
    s = f"{report['passed']}/{report['expected']} acceptance tests passed"
    if report["failed"]:
        s += "; not passing: " + ", ".join(report["failed"][:8])
    if report["missing"]:
        s += "; missing from the output: " + ", ".join(report["missing"][:8])
    return s
