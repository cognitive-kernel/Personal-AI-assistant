"""Automated, sandboxed verification for source-provided Python doctest examples.

Only examples with explicit expected output are runnable. No examples means no test,
and no test never counts as success. Execution uses the existing no-network Docker sandbox.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
from typing import Any

from . import db, sandbox
from .learning_evidence_gate import is_allowlisted_primary_source


def count_doctest_examples(text: str) -> int:
    """Count explicit doctest prompts; ordinary code fences are not treated as tests."""
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    return len(re.findall(r"^\s*>>>\s+", text, re.MULTILINE))


def extract_source_assertion_blocks(text: str, *, max_blocks: int = 5) -> list[str]:
    """Extract small fenced Python examples with assertions and a restricted syntax.

    These are source-authored exploratory tests, not trusted proof of factual claims.
    They run only in the no-network resource-limited Docker sandbox. Imports, loops
    with unbounded control flow, filesystem/process access, and reflective builtins
    are excluded to reduce risk from untrusted tutorial pages.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    if isinstance(max_blocks, bool) or not isinstance(max_blocks, int) or not 1 <= max_blocks <= 10:
        raise ValueError("max_blocks must be between 1 and 10.")
    dangerous_calls = {
        "eval", "exec", "compile", "open", "input", "breakpoint", "exit", "quit",
        "getattr", "setattr", "delattr", "globals", "locals", "vars", "__import__",
    }
    rejected_nodes = (
        ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.While,
        ast.AsyncFor, ast.With, ast.AsyncWith,
    )
    blocks: list[str] = []
    total_chars = 0
    pattern = re.compile(r"^\s*```(?:python|py)\s*\n(.*?)^\s*```\s*$", re.IGNORECASE | re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(text):
        code = match.group(1).strip()
        if not code or len(code) > 8000:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        nodes = list(ast.walk(tree))
        # Require a top-level, behavior-related comparison. Bare assertions such as
        # assert True or assert 1 == 1 must not make an empty example pass.
        meaningful_assertions = [
            statement for statement in tree.body
            if isinstance(statement, ast.Assert)
            and isinstance(statement.test, ast.Compare)
            and any(
                isinstance(node, (ast.Name, ast.Call, ast.Subscript, ast.Attribute))
                for node in ast.walk(statement.test)
            )
        ]
        if not meaningful_assertions:
            continue
        unsafe = False
        for node in nodes:
            if isinstance(node, rejected_nodes):
                unsafe = True
                break
            if isinstance(node, ast.Name) and (
                node.id.startswith("__") or node.id in dangerous_calls
            ):
                unsafe = True
                break
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                unsafe = True
                break
            if isinstance(node, ast.Call):
                function_name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if function_name in dangerous_calls:
                    unsafe = True
                    break
        if unsafe:
            continue
        if len(blocks) >= max_blocks or total_chars + len(code) > 20000:
            break
        blocks.append(code)
        total_chars += len(code)
    return blocks


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
            """SELECT c.topic, c.content, c.source_urls_json, c.content_sha256,
                      s.content AS source_content
               FROM temporary_knowledge_candidates AS c
               LEFT JOIN learning_source_text AS s ON s.job_id=c.job_id
               WHERE c.id=?""",
            (candidate_id,),
        ).fetchone()
    if candidate is None:
        raise KeyError(candidate_id)
    # Only execute examples present in the original supplied/retrieved source.
    # Do not let the generated lesson author its own test oracle.
    source_text = candidate["source_content"] or ""
    doctest_count = count_doctest_examples(source_text)
    assertion_blocks = extract_source_assertion_blocks(source_text)
    example_count = doctest_count + len(assertion_blocks)
    if example_count == 0:
        from .knowledge_review import record_review_attempt
        record_review_attempt(candidate_id, channel="sandbox", status="no_examples",
                              details={"tests": 0, "reason": "no_safe_source_examples"})
        return {"candidate_id": candidate_id, "status": "no_examples", "tests": 0, "promoted": False}
    try:
        source_urls = json.loads(candidate["source_urls_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        source_urls = []
    trusted_source_urls = [
        url for url in source_urls
        if isinstance(url, str) and is_allowlisted_primary_source(url)
    ]
    if not trusted_source_urls:
        from .knowledge_review import record_review_attempt
        record_review_attempt(candidate_id, channel="sandbox", status="unavailable",
                              details={"tests": 0, "reason": "missing_allowlisted_primary_source_oracle"})
        return {"candidate_id": candidate_id, "status": "missing_source_oracle", "tests": 0, "promoted": False}
    if len(trusted_source_urls) != 1:
        from .knowledge_review import record_review_attempt
        record_review_attempt(
            candidate_id, channel="sandbox", status="unavailable",
            details={
                "tests": 0,
                "reason": "source_attribution_ambiguous",
                "trusted_source_count": len(trusted_source_urls),
            },
        )
        return {
            "candidate_id": candidate_id, "status": "source_attribution_ambiguous",
            "tests": 0, "promoted": False,
        }
    oracle_url = trusted_source_urls[0]
    image = os.environ.get("SANDBOX_PYTHON_IMAGE", "").strip()
    docker = os.environ.get("SANDBOX_DOCKER_EXECUTABLE", "").strip() or shutil.which("docker") or ""
    if not image or not docker:
        from .knowledge_review import record_review_attempt
        record_review_attempt(candidate_id, channel="sandbox", status="sandbox_unconfigured",
                              details={"tests": 0, "docker_configured": bool(docker), "image_configured": bool(image)})
        return {"candidate_id": candidate_id, "status": "sandbox_unconfigured", "tests": 0, "promoted": False}

    # Treat original source as data and execute only in the bounded no-network container.
    lesson_literal = json.dumps(source_text, ensure_ascii=True)
    blocks_literal = json.dumps(assertion_blocks, ensure_ascii=True)
    runner = (
        "import ast, doctest, json\n"
        f"lesson = json.loads({json.dumps(lesson_literal)})\n"
        f"blocks = json.loads({json.dumps(blocks_literal)})\n"
        "parser = doctest.DocTestParser()\n"
        "test = parser.get_doctest(lesson, {}, 'candidate_source_examples', 'lesson.txt', 0)\n"
        "runner = doctest.DocTestRunner(verbose=False)\n"
        "runner.run(test)\n"
        "block_failures = 0\n"
        "for index, code in enumerate(blocks):\n"
        " try:\n"
        "  tree = ast.parse(code)\n"
        "  exec(compile(tree, f'<source-assertion-{index}>', 'exec'), {'__name__': '__main__'})\n"
        " except BaseException:\n"
        "  block_failures += 1\n"
        "print(json.dumps({'doctest_attempted': runner.tries, 'doctest_failed': runner.failures, "
        "'assertion_blocks_attempted': len(blocks), 'assertion_blocks_failed': block_failures}, sort_keys=True))\n"
        "raise SystemExit(0 if runner.tries + len(blocks) > 0 and runner.failures == 0 and block_failures == 0 else 1)\n"
    )
    from .knowledge_review import record_review_attempt
    try:
        result = sandbox.run_python(
            runner, docker_executable=docker, image=image, timeout_seconds=15, max_output_bytes=8192,
        )
    except Exception as exc:
        record_review_attempt(
            candidate_id, channel="sandbox", status="failed",
            details={"tests": example_count, "error_type": type(exc).__name__},
        )
        raise
    try:
        actual = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError):
        actual = {"status": result.status, "stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]}
    expected = {
        "doctest_attempted": doctest_count,
        "doctest_failed": 0,
        "assertion_blocks_attempted": len(assertion_blocks),
        "assertion_blocks_failed": 0,
    }
    passed = result.status == "succeeded" and actual == expected
    suite_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    run_status = "passed" if passed else ("failed" if result.status in {"succeeded", "failed"} else "unavailable")
    if run_status == "failed" and isinstance(actual, dict):
        actual["runner_stderr"] = (result.stderr or result.reason or "sandbox execution failed")[-2000:]
    record_review_attempt(
        candidate_id, channel="sandbox",
        status="completed" if run_status in {"passed", "failed"} else "unavailable",
        details={"tests": example_count, "doctest_examples": doctest_count,
                 "assertion_blocks": len(assertion_blocks), "run_status": run_status,
                 "suite_sha256": suite_digest, "duration_ms": result.duration_ms},
    )
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
    promoted_to_knowledge = False
    trusted_oracle = is_allowlisted_primary_source(oracle_url)
    if run_status == "failed":
        with db.learning_connection() as conn:
            conn.execute(
                "UPDATE temporary_knowledge_candidates SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='awaiting_test'",
                (candidate_id,),
            )
    if trusted_oracle and run_status == "failed":
        # A failing source example is useful failure experience, but a passing
        # example suite only validates those examples; it cannot verify the full lesson.
        from .learning import record_candidate_test_results
        expected_digest = hashlib.sha256(json.dumps(
            expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()
        plan = [{
            "test": "source-doctest-suite",
            "purpose": "Record the failure of safe expected-output examples embedded in the cited primary source.",
            "oracle_kind": "source_example",
            "oracle_ref": oracle_url,
            "expected": expected,
            "oracle_sha256": expected_digest,
        }]
        result_record = [{
            "test": "source-doctest-suite",
            "runner": "sandbox",
            "passed": False,
            "expected": expected,
            "actual": actual,
            "oracle_sha256": expected_digest,
        }]
        failed_record = record_candidate_test_results(candidate_id, plan, result_record)
        current_status = str(failed_record["status"])
        with db.learning_connection() as conn:
            latest_experience = conn.execute(
                "SELECT id FROM learning_experiences WHERE problem LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%نامزد دانشی {candidate_id}%",),
            ).fetchone()
            experience_id = int(latest_experience["id"]) if latest_experience else None
    elif run_status == "passed":
        # Preserve a successful scoped test as evidence without promoting the entire
        # generated lesson. A separate claim-complete trusted suite must authorize promotion.
        current_status = "awaiting_test"
        promoted_to_knowledge = False
    if run_status == "failed" and not trusted_oracle:
        current_status = "failed"
        # Untrusted-source failures are still durable experiences, but cannot promote
        # knowledge through the trusted-oracle gate.
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
        "doctest_examples": doctest_count,
        "assertion_blocks": len(assertion_blocks),
        "passed": passed,
        "candidate_status": current_status,
        "verification_scope": "explicit source doctests and restricted fenced Python assertion blocks only",
        "promoted": promoted_to_knowledge,
        "failure_experience_id": experience_id,
        "note": "Promotion is limited to the declared source-example scope; this does not establish universal truth or verify every sentence in the lesson.",
    }
