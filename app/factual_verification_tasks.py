"""Consent-gated Docker verification for sourced Python factual claims."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
from typing import Any, Callable
from urllib.parse import urlparse

from . import factual_knowledge, factual_semantic_review, sandbox_consent, sandbox_tasks
from .db import learning_connection
from .task_worker import TaskContext

FACTUAL_VERIFY_TASK_TYPE = "factual.verify_claim"
_ALLOWED_PAYLOAD_KEYS = {
    "knowledge_id", "test_name", "source_code", "timeout_seconds", "max_output_bytes",
    "oracle_reviewed", "oracle_review_note", "oracle_review_id",
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
        oracle_review_id = payload.get("oracle_review_id")
        if isinstance(oracle_review_id, bool) or not isinstance(oracle_review_id, int) or oracle_review_id < 1:
            raise ValueError("oracle_review_id must identify a persisted oracle review.")
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
        with learning_connection() as connection:
            oracle_review = factual_knowledge.get_oracle_review(
                connection,
                oracle_review_id,
                knowledge_id=knowledge_id,
                claim_hash=candidate["claim_hash"],
                test_source_hash=original_hash,
            )
        if oracle_review is None:
            raise ValueError("Persisted independent-oracle review does not match this claim revision and exact test source.")
        with learning_connection() as connection:
            evidence_rows = connection.execute(
                """SELECT source_type, source_ref, source_hash, excerpt, applicability_json
                   FROM factual_evidence
                   WHERE knowledge_id=? ORDER BY id LIMIT 5""",
                (knowledge_id,),
            ).fetchall()
        usable_evidence = []
        for evidence_row in evidence_rows:
            evidence_item = dict(evidence_row)
            parsed_source_url = urlparse(str(evidence_item.get("source_ref", "")))
            excerpt = evidence_item.get("excerpt")
            if (
                evidence_item.get("source_type") in {"official_docs", "source_code"}
                and parsed_source_url.scheme == "https"
                and parsed_source_url.hostname
                and isinstance(excerpt, str)
                and excerpt.strip()
            ):
                usable_evidence.append(evidence_item)
        if not usable_evidence:
            raise ValueError("Independent factual review requires a bounded HTTPS source excerpt.")
        evidence = usable_evidence[0]
        excerpt = evidence["excerpt"][:4000]
        excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        try:
            applicability = json.loads(evidence.get("applicability_json") or "{}")
        except (TypeError, ValueError):
            applicability = {}
        source_version = applicability.get("source_version") or applicability.get("version")
        if not isinstance(source_version, str) or not source_version.strip():
            source_version = "unspecified"
            source_path_parts = [part for part in urlparse(evidence["source_ref"]).path.split("/") if part]
            if source_path_parts and source_path_parts[0] in {"2", "3"}:
                source_version = source_path_parts[0]
        try:
            semantic_decision = factual_semantic_review.review_claim_against_source(
                claim=candidate["claim"],
                claim_hash=candidate["claim_hash"],
                source_excerpt=excerpt,
                source_url=evidence["source_ref"],
                source_hash=excerpt_hash,
                source_version=source_version,
                claim_version=candidate["environment_version"],
                environment_name=candidate["environment_name"],
                environment_version=candidate["environment_version"],
                test_source=source_code,
            )
        except (ValueError, RuntimeError) as exc:
            raise ValueError("Independent factual semantic review failed closed: " + str(exc)[:300]) from exc
        if not semantic_decision.accepted:
            raise ValueError("Independent factual review did not pass the fail-closed rubric.")
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
            "oracle_review_id": oracle_review_id,
            "semantic_rubric_version": semantic_decision.rubric_version,
            "semantic_review_score": semantic_decision.score,
            "semantic_review_verdict": semantic_decision.verdict,
            "semantic_review_confidence": semantic_decision.confidence,
            "semantic_evaluator_model": semantic_decision.evaluator_model,
            "semantic_evaluator_digest": semantic_decision.evaluator_digest,
            "oracle_review_note": oracle_review_note.strip(),
            "oracle_source_url": oracle_review["oracle_source_url"],
            "oracle_source_hash": oracle_review["oracle_source_hash"],
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
            "oracle_review_id": oracle_review_id,
            "semantic_rubric_version": semantic_decision.rubric_version,
            "semantic_review_score": semantic_decision.score,
            "semantic_review_verdict": semantic_decision.verdict,
            "semantic_evaluator_model": semantic_decision.evaluator_model,
            "semantic_evaluator_digest": semantic_decision.evaluator_digest,
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
