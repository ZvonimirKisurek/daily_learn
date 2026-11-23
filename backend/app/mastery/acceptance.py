"""Acceptance tests for hands-on tasks: static rules, sandbox verification, and grading from the learner's test run.

The pipeline:
  generation  - the model writes {interface, test file, reference solution} for the task it just planned;
                `static_problems` enforces the rules below (repair loop on failure), then `verify` RUNS the suite against
                the reference in the sandbox (repair loop on failing tests). The suite is stored with status
                "verified" (it passed), or "unverified" (the sandbox is disabled or the server lacks a library such as torch).
  learner     - runs `python -m pytest -v test_acceptance.py` (or tools/mle_check.py) and submits code + output.
  grading     - `pytest_report.parse` counts PASSED per expected test; `apply_tests` adds a code-scored criterion to the
                AI evaluation. A task with tests can only pass if EVERY acceptance test passed.

Rules for a suite (each is a sentence the model gets back if it breaks it):
  * the test file is `test_*.py`, inside the project folder, next to the modules it imports (pytest's default
    "prepend" import mode then finds them with no packaging);
  * 3-8 top-level `def test_*` functions (no classes, so `file::name` in `pytest -v` output maps 1:1 to a stored name);
  * imports: the standard library, a short allow-list of scientific libraries, and the first-party modules provided
    in reference_files; no network or process-spawning modules anywhere;
  * every reference file is a .py path from the task's project_layout and parses.
"""
from __future__ import annotations

import ast
import logging
import posixpath
import re
import sys
from typing import TYPE_CHECKING

from app.mastery import sandbox

if TYPE_CHECKING:
    from app.curriculum.schemas import AcceptanceSuite

log = logging.getLogger(__name__)

THIRD_PARTY = {"numpy", "scipy", "sklearn", "pandas", "torch", "pytest", "matplotlib"}
FORBIDDEN = {"socket", "subprocess", "requests", "httpx", "urllib", "urllib3", "http", "ftplib", "smtplib", "telnetlib", "ctypes",
             "multiprocessing", "asyncio", "aiohttp", "webbrowser", "paramiko", "openai", "psycopg", "shutil"}
FORBIDDEN_CALLS = re.compile(r"\b(?:os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|os\.fork|__import__|eval\s*\(|exec\s*\()")
_TEST_PATH = re.compile(r"^(?:[A-Za-z0-9_\-]+/)*test_[A-Za-z0-9_]+\.py$")


def _imports(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def suite_test_names(code: str) -> list[str]:
    tree = ast.parse(code)
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")]


def static_problems(suite: "AcceptanceSuite", project_files: frozenset[str] | None) -> list[str]:
    problems: list[str] = []
    tp = suite.test_path.strip()
    if not _TEST_PATH.match(tp) or ".." in tp:
        problems.append(f"test_path {tp!r} must be a relative path to a test_*.py file, e.g. 'lab/test_acceptance.py'")
    test_dir = posixpath.dirname(tp)
    ref_paths = [f.path.strip() for f in suite.reference_files]
    for path in ref_paths:
        if not path.endswith(".py") or ".." in path or path.startswith("/"):
            problems.append(f"reference file {path!r} must be a relative .py path")
        elif project_files is not None and path not in project_files:
            problems.append(f"reference file {path!r} is not in the task's project_layout {sorted(project_files)}")
        if path == tp:
            problems.append("the test file must not also be a reference file")
    if len(set(ref_paths)) != len(ref_paths):
        problems.append("reference_files paths must be unique")
    first_party = {posixpath.splitext(posixpath.basename(p))[0] for p in ref_paths if posixpath.dirname(p) == test_dir}

    try:
        tree = ast.parse(suite.test_code)
    except SyntaxError as exc:
        return problems + [f"test_code has a syntax error at line {exc.lineno}: {exc.msg}"]
    names = suite_test_names(suite.test_code)
    if not 3 <= len(names) <= 8:
        problems.append(f"test_code must define 3-8 top-level test_* functions (found {len(names)}); do not put tests in classes")
    if any(isinstance(n, ast.ClassDef) and n.name.startswith("Test") for n in tree.body):
        problems.append("do not use Test* classes; write top-level test_* functions")
    imported = _imports(tree)
    if not imported & first_party:
        problems.append(f"the tests must import the learner's code: one of the reference modules in {test_dir or 'the project root'} ({sorted(first_party) or 'none provided there'})")
    for name in sorted(imported - first_party):
        if name in FORBIDDEN:
            problems.append(f"test_code may not import {name!r} (no network, processes or filesystem tricks)")
        elif name not in sys.stdlib_module_names and name not in THIRD_PARTY:
            problems.append(f"test_code imports {name!r}, which is neither the standard library, an allowed library {sorted(THIRD_PARTY)}, "
                            f"nor a reference module next to the test file")
    for f in suite.reference_files:
        try:
            ref_imports = _imports(ast.parse(f.content))
        except SyntaxError as exc:
            problems.append(f"{f.path} has a syntax error at line {exc.lineno}: {exc.msg}")
            continue
        bad = sorted(ref_imports & FORBIDDEN)
        if bad:
            problems.append(f"{f.path} may not import {bad}")
    if FORBIDDEN_CALLS.search(suite.test_code) or any(FORBIDDEN_CALLS.search(f.content) for f in suite.reference_files):
        problems.append("do not use os.system/os.popen/exec/eval/__import__")
    return problems


def verify(suite: "AcceptanceSuite", *, enabled: bool, timeout_s: int) -> tuple[str, str, list[str]]:
    """Run the suite against the reference. Returns (status, detail, problems): problems non-empty means 'repair this'."""
    if not enabled:
        return "unverified", "sandbox verification is disabled (VERIFY_ACCEPTANCE_TESTS=false)", []
    files = {f.path: f.content for f in suite.reference_files}
    files[suite.test_path] = suite.test_code
    res = sandbox.run_suite(files, suite.test_path, timeout_s=timeout_s)
    first_party = {posixpath.splitext(posixpath.basename(f.path))[0] for f in suite.reference_files}
    if res.status == "unavailable" and res.missing_module not in first_party:
        log.warning("acceptance suite not verifiable here: %s is not installed", res.missing_module)
        return "unverified", f"'{res.missing_module}' is not installed on the server, so the suite could not be run there", []
    if res.status == "passed":
        return "verified", f"all {res.passed} tests passed against the reference solution", []
    if res.status == "timeout":
        return "failed", "timeout", [f"the suite took longer than {timeout_s}s against the reference solution; use tiny data and fewer iterations"]
    tail = res.output[-2500:]
    return "failed", "tests fail", [f"the tests do NOT pass against your reference solution ({res.passed} passed, {res.failed} failed). "
                                    f"Fix the tests or the reference so they agree with the task's instructions. pytest output (tail):\n{tail}"]


def stored(suite: "AcceptanceSuite", status: str, detail: str) -> tuple[dict, list[dict]]:
    """What the DB keeps: the public part (shown to the learner) and the hidden reference solution."""
    test_dir = posixpath.dirname(suite.test_path)
    public = {"path": suite.test_path, "code": suite.test_code, "test_names": suite_test_names(suite.test_code),
              "interface": suite.interface, "status": status, "detail": detail,
              "run_command": f"cd {test_dir} && python -m pytest -v {posixpath.basename(suite.test_path)}" if test_dir
              else f"python -m pytest -v {suite.test_path}"}
    return public, [f.model_dump() for f in suite.reference_files]


TEST_CRITERION = "Acceptance tests pass"
TEST_POINTS = 6


def apply_tests(evaluation: dict, report: dict, pass_score: int) -> dict:
    """Fold the parsed test report into the AI evaluation: one extra, code-scored, must-pass criterion.

    points = round(TEST_POINTS * passed / expected); the task passes only if the AI rubric passed AND every test passed.
    The score is recomputed over all criteria with the same arithmetic as schemas.compute_grade."""
    from app.mastery.pytest_report import describe

    points = round(TEST_POINTS * report["passed"] / report["expected"]) if report["expected"] else 0
    criteria = [c for c in evaluation["criteria"] if c["criterion"] != TEST_CRITERION]
    criteria.append({"criterion": TEST_CRITERION, "points": points, "max_points": TEST_POINTS,
                     "evidence": describe(report) + " (parsed from your pytest -v output by code, not judged by the AI)"})
    earned = sum(c["points"] for c in criteria)
    total = sum(c["max_points"] for c in criteria)
    score = max(1, round(10 * earned / total))
    passed = bool(evaluation["passed"]) and report["all_passed"] and score >= pass_score
    out = {**evaluation, "criteria": criteria, "score": score, "passed": passed, "tests": report}
    if not report["all_passed"]:
        out["problems"] = [describe(report)] + [p for p in evaluation.get("problems", []) if p][:5]
        out["summary"] = ("Not passed yet: " + describe(report) + ". " + evaluation["summary"])[:600]
    return out
