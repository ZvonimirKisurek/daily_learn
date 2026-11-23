#!/usr/bin/env python3
"""mle_check: run a hands-on task's acceptance tests on YOUR machine, then (optionally) submit for grading.

Standard library only: copy this file anywhere. It talks to the MLE-Sprint API (not to the Next.js app).

    python tools/mle_check.py TASK_ID                      # fetch the suite into ./<path>, run pytest -v, show results
    python tools/mle_check.py TASK_ID --dir ~/labs/day12   # your project root (the folder that contains e.g. lab/)
    python tools/mle_check.py TASK_ID --submit             # run, then submit your project files + the real pytest output

TASK_ID is the uuid in the practice page URL (/practice/<TASK_ID>).
API: --api (default $API_URL or http://localhost:8000); token: --token (default $API_TOKEN).

What gets submitted with --submit: every file from the task's project_layout that exists under --dir (except the
acceptance test file itself), concatenated with '# ==== path ====' headers, plus the pytest output. Grading then
counts PASSED tests by code and the AI reviews the code; the task passes only if every acceptance test passes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def call(api: str, token: str, method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(api.rstrip("/") + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        sys.exit(f"API error {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach the API at {api}: {e.reason}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task_id")
    ap.add_argument("--dir", default=".", help="project root (default: current directory)")
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("API_TOKEN", ""))
    ap.add_argument("--submit", action="store_true", help="submit code + pytest output for grading after running")
    ap.add_argument("--keep-tests", action="store_true", help="never overwrite an existing acceptance test file")
    args = ap.parse_args()

    detail = call(args.api, args.token, "GET", f"/api/tasks/{args.task_id}")
    suite, task = detail.get("acceptance_tests"), detail.get("hands_on")
    if not task:
        sys.exit("that task is not a hands-on task")
    if not suite:
        sys.exit("this task has no acceptance tests; submit through the web page")
    root = Path(args.dir).expanduser().resolve()
    test_file = (root / suite["path"]).resolve()
    if root not in test_file.parents:
        sys.exit(f"refusing to write outside {root}: {suite['path']}")

    if test_file.exists() and test_file.read_text() != suite["code"]:
        if args.keep_tests:
            print(f"note: {suite['path']} differs from the official suite (kept because of --keep-tests)")
        else:
            test_file.with_suffix(".py.bak").write_text(test_file.read_text())
            test_file.write_text(suite["code"])
            print(f"updated {suite['path']} to the official suite (your old copy: {test_file.with_suffix('.py.bak').name})")
    elif not test_file.exists():
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(suite["code"])
        print(f"wrote {suite['path']}")

    print("\nInterface the tests expect:\n" + "\n".join(f"  - {i}" for i in suite["interface"]) + "\n")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", test_file.name],
                          cwd=test_file.parent, capture_output=True, text=True, check=False)
    output = proc.stdout + proc.stderr
    print(output)
    if not args.submit:
        print("all acceptance tests pass - submit with --submit" if proc.returncode == 0 else "fix the failing tests, then run again")
        return proc.returncode

    parts, total = [], 0
    for rel in task["project_layout"]:
        p = (root / rel).resolve()
        if p == test_file or root not in p.parents or not p.is_file():
            continue
        text = f"# ==== {rel} ====\n{p.read_text(errors='replace')}\n"
        if total + len(text) > 19000:
            print(f"note: {rel} skipped (submission size limit)")
            continue
        parts.append(text)
        total += len(text)
    if not parts:
        sys.exit("none of the project_layout files exist under --dir; nothing to submit")
    print(f"submitting {len(parts)} file(s) and the pytest output for grading (this takes 10-60 s)...")
    res = call(args.api, args.token, "POST", f"/api/tasks/{args.task_id}/submit",
               {"submission": "".join(parts), "test_output": output[-30000:]}, timeout=180)
    ev = res["evaluation"]
    tests = ev.get("tests") or {}
    print(f"\n{'PASSED' if res['passed'] else 'NOT PASSED'}  score {ev['score']}/10  tests {tests.get('passed', '?')}/{tests.get('expected', '?')}"
          f"  attempts {res['attempts_used']}/{res['attempts_max']}\n\n{ev['summary']}\n")
    for item in ev.get("how_to_improve", []):
        print(f"  - {item}")
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
