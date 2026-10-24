"""Idempotent conversion of failed candidate tests into reusable warning experiences.

Failure records are searchable warnings only. They are never marked verified and
never change the candidate's trust or lifecycle state.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from .db import init_databases, learning_connection


def _validate_relationship_id(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer or None.")
    return value


def record_candidate_test_failure(
    candidate_id: int,
    *,
    topic: str,
    content_sha256: str,
    test_plan_sha256: str,
    outcomes: list[dict[str, Any]],
    related_knowledge_id: int | None = None,
    related_consensus_id: int | None = None,
) -> dict[str, Any]:
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic is required.")
    related_knowledge_id = _validate_relationship_id(related_knowledge_id, "related_knowledge_id")
    related_consensus_id = _validate_relationship_id(related_consensus_id, "related_consensus_id")
    for digest in (content_sha256, test_plan_sha256):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Content and plan hashes must be lowercase SHA-256 digests.")
    if not isinstance(outcomes, list) or not outcomes or len(outcomes) > 3:
        raise ValueError("One to three test outcomes are required.")
    bounded: list[dict[str, Any]] = []
    for item in outcomes:
        if not isinstance(item, dict) or not isinstance(item.get("test"), str) or not item["test"].strip():
            raise ValueError("Every outcome must name its test.")
        if type(item.get("passed")) is not bool:
            raise ValueError("Every outcome must have a boolean passed field.")
        bounded.append({
            "test": item["test"][:120],
            "passed": item["passed"],
            "status": str(item.get("status", "unknown"))[:40],
            "expected_stdout_sha256": str(item.get("expected_stdout_sha256", ""))[:64],
            "actual_stdout_sha256": str(item.get("actual_stdout_sha256", ""))[:64],
            "stderr_sha256": str(item.get("stderr_sha256", ""))[:64],
            "reason": str(item.get("reason", ""))[:160],
        })
    canonical = json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    failure_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    title = ("Test failure: " + topic.strip())[:300]
    problem = (
        f"Candidate {candidate_id} failed a human-reviewed sandbox test. "
        f"content_sha256={content_sha256}; test_plan_sha256={test_plan_sha256}; "
        f"failure_sha256={failure_sha256}; related_knowledge_id={related_knowledge_id}; "
        f"related_consensus_id={related_consensus_id}. This is a warning record, not proof that the lesson is false."
    )
    hypothesis = "The reviewed test output did not match its human-attested expected output or execution policy."
    plan = [{
        "test": item["test"],
        "oracle": "human-attested expected stdout; not an independent domain oracle",
        "expected_stdout_sha256": item["expected_stdout_sha256"],
    } for item in bounded]
    results = [{
        "test": item["test"],
        "passed": item["passed"],
        "status": item["status"],
        "actual_stdout_sha256": item["actual_stdout_sha256"],
        "stderr_sha256": item["stderr_sha256"],
        "reason": item["reason"],
    } for item in bounded]
    takeaway = (
        f"Review candidate {candidate_id} and its test plan before reuse. "
        "A failed sandbox check must remain a warning; do not promote or automatically rewrite the lesson."
    )
    init_databases()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id, status FROM learning_experiences WHERE topic=? AND problem=? AND hypothesis=?",
            (title, problem, hypothesis),
        ).fetchone()
        if existing is not None:
            return {"experience_id": int(existing["id"]), "created": False, "failure_sha256": failure_sha256}
        cursor = connection.execute(
            """INSERT INTO learning_experiences(
                   topic, problem, hypothesis, status, source_evidence_json,
                   test_plan_json, test_results_json, takeaway, version,
                   related_knowledge_id, related_consensus_id
               ) VALUES (?, ?, ?, 'failed', '[]', ?, ?, ?, 1, ?, ?)""",
            (title, problem, hypothesis, json.dumps(plan, ensure_ascii=False),
             json.dumps(results, ensure_ascii=False), takeaway,
             related_knowledge_id, related_consensus_id),
        )
        experience_id = int(cursor.lastrowid)
    return {"experience_id": experience_id, "created": True, "failure_sha256": failure_sha256}


def record_candidate_channel_failure(
    candidate_id: int,
    *,
    topic: str,
    content_sha256: str,
    channel: str,
    status: str,
    error_type: str = "unknown",
    related_knowledge_id: int | None = None,
    related_consensus_id: int | None = None,
) -> dict[str, Any]:
    """Persist an idempotent web/peer channel failure as a cautionary, searchable experience."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic is required.")
    related_knowledge_id = _validate_relationship_id(related_knowledge_id, "related_knowledge_id")
    related_consensus_id = _validate_relationship_id(related_consensus_id, "related_consensus_id")
    if not isinstance(content_sha256, str) or len(content_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in content_sha256
    ):
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest.")
    if channel not in {"research_web", "peer_ai"}:
        raise ValueError("Only web and peer-AI review failures are supported.")
    if status not in {"failed", "unavailable"}:
        raise ValueError("Only failed or unavailable channel outcomes may be recorded.")
    safe_error_type = (
        (re.sub(r"[^A-Za-z0-9_.-]", "_", error_type)[:80] or "unknown")
        if isinstance(error_type, str)
        else "unknown"
    )
    identity = {
        "candidate_id": candidate_id,
        "content_sha256": content_sha256,
        "channel": channel,
        "status": status,
        "error_type": safe_error_type,
        "related_knowledge_id": related_knowledge_id,
        "related_consensus_id": related_consensus_id,
    }
    failure_sha256 = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    channel_label = "web research" if channel == "research_web" else "peer-AI review"
    title = (f"{channel_label} failure: {topic.strip()}")[:300]
    problem = (
        f"Candidate {candidate_id} {channel_label} returned {status}; "
        f"content_sha256={content_sha256}; failure_sha256={failure_sha256}. "
        "This is an operational review warning, not evidence that the lesson is false."
    )
    hypothesis = (
        f"The {channel_label} channel did not produce usable review evidence "
        f"(error_type={safe_error_type}); retry or inspect channel configuration before relying on its absence."
    )
    plan = [{
        "channel": channel,
        "status": status,
        "error_type": safe_error_type,
        "content_sha256": content_sha256,
        "failure_sha256": failure_sha256,
    }]
    results = [{
        "channel": channel,
        "status": status,
        "error_type": safe_error_type,
        "usable_evidence": False,
    }]
    takeaway = (
        f"Treat the {channel_label} result for candidate {candidate_id} as unavailable evidence. "
        "Do not infer truth or falsity, and do not promote the candidate because another channel failed."
    )
    init_databases()
    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM learning_experiences WHERE topic=? AND problem=? AND hypothesis=?",
            (title, problem, hypothesis),
        ).fetchone()
        if existing is not None:
            return {"experience_id": int(existing["id"]), "created": False, "failure_sha256": failure_sha256}
        cursor = connection.execute(
            """INSERT INTO learning_experiences(
                   topic, problem, hypothesis, status, source_evidence_json,
                   test_plan_json, test_results_json, takeaway, version,
                   related_knowledge_id, related_consensus_id
               ) VALUES (?, ?, ?, 'failed', '[]', ?, ?, ?, 1, ?, ?)""",
            (title, problem, hypothesis, json.dumps(plan, ensure_ascii=False),
             json.dumps(results, ensure_ascii=False), takeaway,
             related_knowledge_id, related_consensus_id),
        )
        experience_id = int(cursor.lastrowid)
    return {"experience_id": experience_id, "created": True, "failure_sha256": failure_sha256}
