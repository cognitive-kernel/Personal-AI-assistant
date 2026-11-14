"""Independent aggregate accuracy measurement and autonomous acceptance gate."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .learning_policy_errors import LearningPolicyError

LEARNING_PATHS = frozenset({
    "ai_learning",
    "basic_learning",
    "curriculum_learning",
    "experience_system",
})
VERIFICATION_METHODS = frozenset({
    "official_source_comparison",
    "deterministic_oracle",
    "sandbox_test",
    "independent_model",
    "human_adjudication",
})


def _required_text(record: dict[str, Any], field: str, *, maximum: int = 2000) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LearningPolicyError(f"{field} must contain 1–{maximum} characters.")
    return value.strip()


def evaluate_aggregate_learning_accuracy(records: Any) -> dict[str, Any]:
    """Calculate the cross-path accuracy gate from independently evidenced evaluations.

    The gate is strictly greater than 95%, pools the four learning paths, and requires
    every path to be represented. Empty, duplicated, self-verified, or untraceable
    evaluations cannot enable autonomous acceptance.
    """
    if not isinstance(records, list):
        raise LearningPolicyError("Evaluation records must be a list.")
    seen: set[str] = set()
    counts = {
        path: {"correct": 0, "incorrect": 0, "total": 0}
        for path in sorted(LEARNING_PATHS)
    }
    total_correct = 0
    total = 0
    for index, record in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(record, dict):
            raise LearningPolicyError(f"{prefix} must be an object.")
        sample_id = _required_text(record, "sample_id", maximum=200)
        if sample_id in seen:
            raise LearningPolicyError(f"{prefix}.sample_id is duplicated.")
        seen.add(sample_id)
        path = record.get("learning_path")
        if not isinstance(path, str) or path not in LEARNING_PATHS:
            raise LearningPolicyError(f"{prefix}.learning_path is unsupported.")
        outcome = record.get("outcome")
        if not isinstance(outcome, str) or outcome not in {"correct", "incorrect"}:
            raise LearningPolicyError(f"{prefix}.outcome must be correct or incorrect.")
        method = record.get("verification_method")
        if not isinstance(method, str) or method not in VERIFICATION_METHODS:
            raise LearningPolicyError(f"{prefix}.verification_method is unsupported.")
        _required_text(record, "verifier_id", maximum=200)
        _required_text(record, "evidence_ref", maximum=2048)
        evaluated_at = _required_text(record, "evaluated_at", maximum=80)
        try:
            timestamp = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LearningPolicyError(f"{prefix}.evaluated_at must be ISO-8601.") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise LearningPolicyError(f"{prefix}.evaluated_at must include a timezone.")
        generator_id = record.get("generator_id")
        verifier_id = record["verifier_id"]
        if method == "independent_model":
            generator = _required_text(record, "generator_id", maximum=200)
            if generator.casefold() == verifier_id.strip().casefold():
                raise LearningPolicyError(f"{prefix} cannot be verified by its generating model.")
        counts[path][outcome] += 1
        counts[path]["total"] += 1
        total += 1
        if outcome == "correct":
            total_correct += 1

    accuracy = (total_correct / total) if total else None
    represented_paths = [path for path, value in counts.items() if value["total"] > 0]
    eligible = bool(total) and len(represented_paths) == len(LEARNING_PATHS) and accuracy > 0.95
    return {
        "policy_version": "aggregate-learning-accuracy-v1",
        "threshold_exclusive": 0.95,
        "sample_count": total,
        "correct_count": total_correct,
        "incorrect_count": total - total_correct,
        "accuracy": accuracy,
        "represented_paths": represented_paths,
        "by_path": counts,
        "autonomous_acceptance_eligible": eligible,
        "reason": (
            "aggregate_accuracy_above_95_percent"
            if eligible else
            "insufficient_evidence_or_accuracy_or_learning_path_coverage"
        ),
        "note": (
            "Eligibility applies only to routine learning-content acceptance. "
            "It does not authorize destructive operations, security-sensitive changes, "
            "production actions, or bypass of safety and access controls."
        ),
    }


def record_accuracy_evaluation(
    record: Any,
    *,
    trusted_adapter: bool = False,
) -> dict[str, Any]:
    """Append one independently evidenced accuracy result from a trusted verifier adapter."""
    if trusted_adapter is not True:
        raise LearningPolicyError("Accuracy evaluations may only be written by trusted verifier adapters.")
    evaluate_aggregate_learning_accuracy([record])
    import hashlib
    from .db import learning_connection

    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM learning_accuracy_evaluations WHERE sample_id=?",
            (record["sample_id"].strip(),),
        ).fetchone()
        if existing is not None:
            if existing["record_sha256"] != digest:
                raise LearningPolicyError("An evaluation sample ID cannot be reused with different evidence.")
            return dict(existing)
        cursor = connection.execute(
            """INSERT INTO learning_accuracy_evaluations(
                   sample_id, learning_path, outcome, verification_method, verifier_id,
                   generator_id, evidence_ref, evaluated_at, record_json, record_sha256
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["sample_id"].strip(), record["learning_path"], record["outcome"],
                record["verification_method"], record["verifier_id"].strip(),
                record.get("generator_id"), record["evidence_ref"].strip(),
                record["evaluated_at"].strip(), encoded, digest,
            ),
        )
        row = connection.execute(
            "SELECT * FROM learning_accuracy_evaluations WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


def get_aggregate_learning_accuracy() -> dict[str, Any]:
    """Recompute the current aggregate gate from immutable, hash-checked evaluations."""
    import hashlib
    from .db import learning_connection

    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT record_json, record_sha256 FROM learning_accuracy_evaluations
               ORDER BY id"""
        ).fetchall()
    records = []
    for index, row in enumerate(rows):
        encoded = row["record_json"]
        observed = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if observed != row["record_sha256"]:
            raise LearningPolicyError(
                f"Stored learning accuracy evaluation {index + 1} failed its integrity check."
            )
        try:
            record = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LearningPolicyError(
                f"Stored learning accuracy evaluation {index + 1} is malformed."
            ) from exc
        records.append(record)
    return evaluate_aggregate_learning_accuracy(records)
