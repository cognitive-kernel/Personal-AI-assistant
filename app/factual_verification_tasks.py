"""Consent-gated Docker verification for sourced Python factual claims."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
from typing import Any, Callable

from . import factual_knowledge, sandbox_consent, sandbox_tasks
from .db import learning_connection
from .task_worker import TaskContext

FACTUAL_VERIFY_TASK_TYPE = "factual.verify_claim"
_ALLOWED_PAYLOAD_KEYS = {
    "knowledge_id", "test_name", "source_code", "timeout_seconds", "max_output_bytes",
    "oracle_reviewed", "oracle_review_note",
}
_RUNTIME_MARKER = re.compile(r"^__PAI_PYTHON_VERSION__:(\d+\.\d+\.\d+)$")


def validate_test_harness(source_code: str) -> None:
    """Accept bounded Python assertions only as an explicitly submitted test harness."""
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


def get_verification_candidate(knowledge_id: int) -> dict[str, Any]:
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
        raise ValueError("knowledge_id must be a positive integer.")
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT id, claim, claim_hash, evidence_level, lifecycle_status,
                      domain_name, environment_name, environment_version
               FROM factual_knowledge WHERE id=?""",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            raise KeyError("Factual claim not found.")
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM factual_evidence WHERE knowledge_id=?",
            (knowledge_id,),
        ).fetchone()[0]
    item = dict(row)
    if item["domain_name"] != "python" or item["environment_name"] != "python":
        raise ValueError("The current Docker verification runner supports Python claims only.")
    if item["evidence_level"] != "L1_sourced" or item["lifecycle_status"] != "active":
        raise ValueError("Only active L1_sourced claims can be submitted for sandbox verification.")
    if evidence_count < 1:
        raise ValueError("The claim must have traceable source evidence before verification.")
    return item


def _wrapped_harness(source_code: str) -> tuple[str, str]:
    encoded = json.dumps(source_code, ensure_ascii=False)
    completion_marker = "__PAI_TEST_COMPLETED__:" + secrets.token_hex(16)
    wrapper = (
        "import json as __pai_json, platform as __pai_platform\n"
        "print('__PAI_PYTHON_VERSION__:' + __pai_platform.python_version(), flush=True)\n"
        f"__pai_source = __pai_json.loads({json.dumps(encoded)})\n"
        "exec(compile(__pai_source, '<factual-claim-test>', 'exec'), {'__name__': '__main__'})\n"
        f"print({json.dumps(completion_marker)}, flush=True)\n"
    )
    return wrapper, completion_marker


def _runtime_version(stdout: str, completion_marker: str) -> tuple[str | None, bool, str]:
    lines = stdout.splitlines()
    if not lines:
        return None, False, ""
    match = _RUNTIME_MARKER.fullmatch(lines[0].strip())
    if match is None:
        return None, False, stdout[:2000]
    completed = lines[-1].strip() == completion_marker
    output_lines = lines[1:-1] if completed else lines[1:]
    return match.group(1), completed, "\n".join(output_lines)[:2000]


def _build_handler(
    sandbox_handler: Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]],
) -> Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]]:
    def handle(
        payload: dict[str, Any],
        checkpoint: dict[str, Any] | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != _ALLOWED_PAYLOAD_KEYS:
            raise ValueError("Factual verification payload has missing or unsupported fields.")
        knowledge_id = payload.get("knowledge_id")
        if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
            raise ValueError("knowledge_id must be a positive integer.")
        if payload.get("oracle_reviewed") is not True:
            raise ValueError("A human must explicitly attest that this test is checked against an independent oracle.")
        oracle_review_note = payload.get("oracle_review_note")
        if not isinstance(oracle_review_note, str) or len(oracle_review_note.strip()) < 20 or len(oracle_review_note) > 1000:
            raise ValueError("oracle_review_note must explain the independent reference review in 20–1000 characters.")
        test_name = payload.get("test_name")
        if not isinstance(test_name, str) or not test_name.strip() or len(test_name) > 300:
            raise ValueError("test_name must contain 1–300 characters.")
        source_code = payload.get("source_code")
        validate_test_harness(source_code)
        timeout_seconds = payload.get("timeout_seconds")
        max_output_bytes = payload.get("max_output_bytes")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be an integer from 1 to 120.")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536:
            raise ValueError("max_output_bytes must be an integer from 1 to 65536.")
        if not sandbox_consent.is_enabled():
            raise ValueError("Sandbox execution requires explicit, current user consent.")

        candidate = get_verification_candidate(knowledge_id)
        original_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        wrapped, completion_marker = _wrapped_harness(source_code)
        context.checkpoint({
            "phase": "factual_claim_test_started",
            "knowledge_id": knowledge_id,
            "test_name": test_name,
            "test_source_sha256": original_hash,
            "claim_hash": candidate["claim_hash"],
        })
        result = sandbox_handler({
            "source_code": wrapped,
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
        }, checkpoint, context)
        if context.control_status() != "running":
            raise RuntimeError("Factual claim test stopped before evidence could be recorded.")
        if not sandbox_consent.is_enabled():
            raise RuntimeError("Factual claim test stopped because sandbox consent was revoked.")

        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        runtime_version, harness_completed, _bounded_stdout = _runtime_version(stdout, completion_marker)
        execution_status = str(result.get("execution_status", "unknown"))
        exit_code = result.get("exit_code")
        log_hash = hashlib.sha256((stdout + "\n" + stderr).encode("utf-8")).hexdigest()
        outcome = "inconclusive"
        if runtime_version is None or execution_status == "unavailable":
            outcome = "environment_failure"
        elif runtime_version != candidate["environment_version"]:
            outcome = "environment_failure"
        elif harness_completed and execution_status == "succeeded" and exit_code == 0:
            outcome = "passed"

        summary = {
            "runner": "docker-sandbox",
            "execution_status": execution_status,
            "exit_code": exit_code,
            "runtime_version": runtime_version,
            "configured_image": str(result.get("image", ""))[:200],
            "duration_ms": result.get("duration_ms"),
            "claim_hash_at_start": candidate["claim_hash"],
            "test_source_sha256": original_hash,
            "oracle_reviewed": True,
            "oracle_review_note": oracle_review_note.strip(),
            "output_hash": log_hash,
        }
        summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:2000]
        with learning_connection() as connection:
            current = connection.execute(
                """SELECT claim_hash, evidence_level, lifecycle_status,
                          domain_name, environment_name, environment_version
                   FROM factual_knowledge WHERE id=?""",
                (knowledge_id,),
            ).fetchone()
            if (
                current is None
                or current["claim_hash"] != candidate["claim_hash"]
                or current["evidence_level"] != "L1_sourced"
                or current["lifecycle_status"] != "active"
                or current["domain_name"] != "python"
                or current["environment_name"] != "python"
                or current["environment_version"] != candidate["environment_version"]
            ):
                outcome = "inconclusive"
                summary["reason"] = "Claim identity or eligibility changed during the sandbox run."
                summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:2000]
            factual_knowledge.record_test(
                connection,
                knowledge_id,
                test_kind="unit",
                test_name=test_name.strip(),
                environment_name="python",
                environment_version=runtime_version or "unknown",
                outcome=outcome,
                result_summary=summary_json,
                command_hash=original_hash,
                log_hash=log_hash,
            )
            promoted = False
            if outcome == "passed" and current is not None:
                factual_knowledge.transition_evidence_level(connection, knowledge_id, "L2_tested")
                promoted = True

        result_payload = {
            "knowledge_id": knowledge_id,
            "test_name": test_name.strip(),
            "outcome": outcome,
            "evidence_level_promoted": promoted,
            "runtime_version": runtime_version,
            "test_source_sha256": original_hash,
            "log_sha256": log_hash,
        }
        context.checkpoint({"phase": "factual_claim_test_recorded", **result_payload})
        return result_payload

    return handle


def handler_from_environment(
    sandbox_handler: Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]] | None:
    """Expose claim verification only when the administrator-configured sandbox exists."""
    if sandbox_handler is None:
        sandbox_handler = sandbox_tasks.handler_from_environment()
    if sandbox_handler is None:
        return None
    return _build_handler(sandbox_handler)
