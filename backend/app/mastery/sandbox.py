"""Run an LLM-written test suite against the LLM-written reference solution, to prove the tests are passable.

WHY. A test suite written by a model is itself untested code: it can assert an invented number, import a function the
task never asks for, or contradict the instructions. Shipping such a suite would fail a correct learner. So at
generation time we write the reference solution and the tests into a temporary directory and run pytest. Failures go
back to the model through the repair loop ("test_x failed: AssertionError ...") until the pair is consistent.

SAFETY. This executes model-generated code on the worker, so it is contained as far as plain Python allows:
  * a fresh temporary directory as cwd and HOME, deleted afterwards;
  * an EMPTY environment (no DATABASE_URL, no OPENAI_API_KEY, no tokens; only PATH and a few harmless variables);
  * resource limits on Linux/macOS (CPU seconds, address space, file size, open files) and a wall-clock timeout;
  * `-p no:cacheprovider`, no plugins autoloaded, bytecode writing off.
It is NOT a network sandbox. The inputs are our own model's output, produced from a prompt in which scraped text is
fenced as untrusted, but if you want zero risk set VERIFY_ACCEPTANCE_TESTS=false (suites are then marked
"unverified") or run the worker in a container without egress. See docs/SAFETY.md.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_MISSING = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")


@dataclass
class RunResult:
    status: str  # "passed" | "failed" | "unavailable" | "timeout"
    passed: int
    failed: int
    output: str
    missing_module: str | None = None


def _limits(cpu_s: int, mem_mb: int):  # pragma: no cover - runs in the child process
    def apply():
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
            resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 2**20, mem_mb * 2**20))
            resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 2**20, 50 * 2**20))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except Exception:
            pass
    return apply


def run_suite(files: dict[str, str], test_path: str, *, timeout_s: int = 90, mem_mb: int = 3072) -> RunResult:
    """files: relative path -> content (reference solution + the test file). Runs pytest -v on test_path."""
    with tempfile.TemporaryDirectory(prefix="mle-verify-") as tmp:
        root = Path(tmp).resolve()
        for rel, content in files.items():
            dest = (root / rel).resolve()
            if root not in dest.parents:  # "../../etc/x" never escapes the sandbox directory
                raise ValueError(f"path escapes the project: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        test_file = root / test_path
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root), "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONHASHSEED": "0", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "MPLBACKEND": "Agg", "OMP_NUM_THREADS": "2",
               "LANG": "C.UTF-8"}
        cmd = [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "--no-header", "-rN", "--tb=short", test_file.name]
        try:
            proc = subprocess.run(cmd, cwd=test_file.parent, env=env, capture_output=True, text=True, timeout=timeout_s,
                                  preexec_fn=_limits(timeout_s, mem_mb) if os.name == "posix" else None)
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return RunResult("timeout", 0, 0, out[-4000:] + f"\n[timed out after {timeout_s}s]")
    out = (proc.stdout + proc.stderr)[-8000:]
    passed = len(re.findall(r"::\S+ PASSED", out))
    failed = len(re.findall(r"::\S+ (?:FAILED|ERROR)", out))
    missing = _MISSING.search(out)
    if missing:
        return RunResult("unavailable", passed, failed, out, missing.group(1).split(".")[0])
    return RunResult("passed" if proc.returncode == 0 and passed and not failed else "failed", passed, failed, out)
