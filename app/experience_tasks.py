"""Controlled runner that connects sandbox execution to versioned experience evidence."""
from __future__ import annotations

import ast
from typing import Any, Callable

from . import experience, sandbox_consent, sandbox_tasks
from .task_worker import TaskContext

EXPERIENCE_TEST_TASK_TYPE = "experience.run_test"
_ALLOWED_PAYLOAD_KEYS = {
    "experience_id",
    "test_name",
    "source_code",
    "timeout_seconds",
    "max_output_bytes",
}


def validate_test_harness(source_code: str) -> None:
    """Require a bounded, syntactically valid Python harness with at least one assert."""
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("source_code must be non-empty text.")
    if "\x00" in source_code or len(source_code.encode("utf-8")) > 64 * 1024:
        raise ValueError("source_code exceeds the 64 KiB limit or contains NUL characters.")
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise ValueError("source_code must be valid Python.") from exc
    if not any(isinstance(node, ast.Assert) for node in ast.walk(tree)):
        raise ValueError("Test harness must contain at least one Python assert statement.")


def _validate_experience_test(item: dict[str, Any], test_name: str) -> None:
    if item.get("status") == "verified":
        raise ValueError("Verified experiences are immutable; create a new experience to retest.")
    evidence = item.get("source_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Experience needs source evidence before a test can run.")
    plan = item.get("test_plan")
    if not isinstance(plan, list) or not plan:
        raise ValueError("Experience needs a test plan before a test can run.")
    if not any(
        isinstance(entry, dict)
        and isinstance(entry.get("test"), str)
        and entry["test"].strip() == test_name
        for entry in plan
    ):
        raise ValueError("test_name must exactly match an item in the saved test plan.")


def handler_from_environment() -> Callable[
    [dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]
] | None:
    """Expose the experience runner only when the administrator-configured sandbox exists."""
    sandbox_handler = sandbox_tasks.handler_from_environment()
    if sandbox_handler is None:
        return None

    def handle(
        payload: dict[str, Any],
        checkpoint: dict[str, Any] | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - _ALLOWED_PAYLOAD_KEYS:
            raise ValueError("Experience test payload contains unsupported fields.")
        experience_id = payload.get("experience_id")
        if isinstance(experience_id, bool) or not isinstance(experience_id, int) or experience_id < 1:
            raise ValueError("experience_id must be a positive integer.")
        test_name = payload.get("test_name")
        if not isinstance(test_name, str) or not test_name.strip() or len(test_name) > 300:
            raise ValueError("test_name must contain 1–300 characters.")
        source_code = payload.get("source_code")
        validate_test_harness(source_code)
        timeout_seconds = payload.get("timeout_seconds", 10)
        max_output_bytes = payload.get("max_output_bytes", 32 * 1024)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be an integer from 1 to 120.")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536:
            raise ValueError("max_output_bytes must be an integer from 1 to 65536.")
        if not sandbox_consent.is_enabled():
            raise ValueError("Sandbox execution requires explicit, current user consent.")

        item = experience.get_experience(experience_id)
        _validate_experience_test(item, test_name)
        context.checkpoint({
            "phase": "experience_test_started",
            "experience_id": experience_id,
            "test_name": test_name,
        })
        result = sandbox_handler(
            {
                "source_code": source_code,
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
            },
            checkpoint,
            context,
        )
        if context.control_status() != "running":
            raise RuntimeError("Experience test was stopped before its evidence could be recorded.")
        if not sandbox_consent.is_enabled():
            raise RuntimeError("Experience test was stopped because sandbox consent was revoked.")
        passed = result.get("execution_status") == "succeeded" and result.get("exit_code") == 0
        actual = (
            "stdout:\n" + str(result.get("stdout", ""))[:2000]
            + "\nstderr:\n" + str(result.get("stderr", ""))[:1000]
        )[:4000]
        test_result = {
            "test": test_name,
            "passed": bool(passed),
            "actual": actual,
            "runner": "docker-sandbox",
            "source_sha256": str(result.get("source_sha256", "")),
            "execution_status": str(result.get("execution_status", "unknown")),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "image": str(result.get("image", "")),
        }
        all_results = list(item.get("test_results") or []) + [test_result]
        updated = experience.update_experience(
            experience_id,
            status="verified" if passed else "failed",
            source_evidence=item["source_evidence"],
            test_plan=item["test_plan"],
            test_results=all_results,
            takeaway=item.get("takeaway", ""),
            _controlled_run=True,
        )
        context.checkpoint({
            "phase": "experience_test_recorded",
            "experience_id": experience_id,
            "test_name": test_name,
            "passed": bool(passed),
            "experience_version": updated["version"],
            "experience_status": updated["status"],
        })
        return {
            "experience_id": experience_id,
            "test_name": test_name,
            "passed": bool(passed),
            "experience_version": updated["version"],
            "experience_status": updated["status"],
            "test_result": test_result,
        }

    return handle
