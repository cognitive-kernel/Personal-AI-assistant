"""Automated, sandboxed verification for source-provided Python doctest examples.

Only examples with explicit expected output are runnable. No examples means no test,
and no test never counts as success. Execution uses the existing no-network Docker sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from typing import Any

from . import db, sandbox


def count_doctest_examples(text: str) -> int:
    """Count explicit doctest prompts; ordinary code fences are not treated as tests."""
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    return len(re.findall(r"^\s*>>>\s+", text, re.MULTILINE))


def run_candidate_source_doctests(candidate_id: int) -> dict[str, Any]:
    """Run explicit expected-output examples from a candidate in the isolated sandbox.

    This verifies only the embedded examples, not every factual claim in the lesson.
    It records pass/fail through the existing candidate-test gate; missing Docker or
    missing examples leaves the candidate awaiting tests.
    """
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    db.init_databases()
    with db.learning_connection() as conn:
        candidate = conn.execute(
            """SELECT topic, content, source_urls_json, content_sha256 FROM temporary_knowledge_candidates WHERE id=?""",
            (candidate_id,),
        ).fetchone()
    if candidate is None:
        raise KeyError(candidate_id)
    lesson = candidate["content"]
    example_count = count_doctest_examples(lesson)
    if example_count == 0:
        return {"candidate_id": candidate_id, "status": "no_examples", "tests": 0, "promoted": False}
    try:
        source_urls = json.loads(candidate["source_urls_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        source_urls = []
    oracle_url = next((u for u in source_urls if isinstance(u, str) and u.startswith("https://")), None)
    if not oracle_url:
        return {"candidate_id": candidate_id, "status": "missing_source_oracle", "tests": 0, "promoted": False}
    image = os.environ.get("SANDBOX_PYTHON_IMAGE", "").strip()
    docker = os.environ.get("SANDBOX_DOCKER_EXECUTABLE", "").strip() or shutil.which("docker") or ""
    if not image or not docker:
        return {"candidate_id": candidate_id, "status": "sandbox_unconfigured", "tests": 0, "promoted": False}

    # Encode the lesson as data; it is interpreted only inside the isolated container.
    lesson_literal = json.dumps(lesson, ensure_ascii=True)
    runner = (
        "import doctest, json\n"
        f"lesson = json.loads({json.dumps(lesson_literal)})\n"
        "parser = doctest.DocTestParser()\n"
        "test = parser.get_doctest(lesson, {}, 'candidate_source_examples', 'lesson.txt', 0)\n"
        "runner = doctest.DocTestRunner(verbose=False)\n"
        "runner.run(test)\n"
        "print(json.dumps({'attempted': runner.tries, 'failed': runner.failures}, sort_keys=True))\n"
        "raise SystemExit(0 if runner.tries > 0 and runner.failures == 0 else 1)\n"
    )
    result = sandbox.run_python(
        runner, docker_executable=docker, image=image, timeout_seconds=15, max_output_bytes=8192,
    )
    try:
        actual = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError):
        actual = {"status": result.status, "stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]}
    expected = {"attempted": example_count, "failed": 0}
    passed = result.status == "succeeded" and actual == expected
    suite_digest = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    run_status = "passed" if passed else ("failed" if result.status in {"succeeded", "failed"} else "unavailable")
    actual_json = json.dumps(actual, ensure_ascii=False, sort_keys=True, allow_nan=False)
    expected_json = json.dumps(expected, ensure_ascii=False, sort_keys=True, allow_nan=False)
    with db.learning_connection() as conn:
        conn.execute(
            """INSERT INTO knowledge_test_runs(
                   candidate_id, suite_name, suite_version, suite_sha256, source_sha256,
                   oracle_url, runner_name, run_status, expected_json, actual_json,
                   duration_ms, details
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id, "source-doctest-suite", "1",
                suite_digest, candidate["content_sha256"], oracle_url, "docker-doctest",
                run_status, expected_json, actual_json, result.duration_ms,
                (result.stderr or result.reason or "")[-2000:],
            ),
        )
        current_status = conn.execute(
            "SELECT status FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["status"]
    experience_id = None
    if run_status == "failed":
        # Preserve a failed experiment as a reusable experience, including its oracle and output.
        try:
            from .experience import create_experience, update_experience
            experience = create_experience(
                candidate["topic"],
                f"Sandboxed source-example suite failed for temporary knowledge candidate {candidate_id}.",
                "The candidate's explicit examples should match their cited expected outputs; investigate the first mismatch before retrying.",
            )
            update_experience(
                int(experience["id"]),
                status="failed",
                source_evidence=[{
                    "url": oracle_url,
                    "claim": f"Source-provided doctest examples for candidate {candidate_id} define the expected behavior tested.",
                }],
                test_plan=[{
                    "test": "source-doctest-suite",
                    "oracle_kind": "source_example",
                    "oracle_ref": oracle_url,
                    "expected": expected,
                }],
                test_results=[{
                    "test": "source-doctest-suite",
                    "passed": False,
                    "runner": "docker-doctest",
                    "expected": expected,
                    "actual": actual,
                    "failure_reason": (result.stderr or result.reason or "Example output mismatch")[-2000:],
                }],
                takeaway="Failed examples remain recorded for diagnosis; this failure does not by itself disprove unrelated claims in the lesson.",
            )
            experience_id = int(experience["id"])
        except Exception:
            # The versioned test-run row remains the durable fallback if experience storage fails.
            experience_id = None
    return {
        "candidate_id": candidate_id,
        "status": result.status,
        "tests": example_count,
        "passed": passed,
        "candidate_status": current_status,
        "verification_scope": "explicit doctest examples in this lesson only",
        "promoted": False,
        "failure_experience_id": experience_id,
        "note": "A scoped example test is not sufficient to promote the entire lesson.",
    }
