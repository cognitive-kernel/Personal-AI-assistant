"""Append-only event ledger for sequential curriculum learning methods.

Each status transition is a new immutable row. The module owns event validation,
idempotency, ordering, and transition rules; orchestration remains a separate layer.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .db import learning_connection

METHOD_ORDER = ("ai_learning", "basic_learning", "topic_learning")
METHOD_STATUSES = frozenset({
    "queued", "running", "failed", "passed", "unavailable", "inconclusive",
})
_TERMINAL_STATUSES = frozenset({"failed", "passed", "unavailable", "inconclusive"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_BYTES = 32 * 1024


class MethodAttemptError(ValueError):
    """A method-attempt event violates its durable contract."""


def next_method(method: str) -> str | None:
    """Return the next distinct learning method in the fixed fallback order."""
    if method not in METHOD_ORDER:
        raise MethodAttemptError("Unsupported learning method.")
    index = METHOD_ORDER.index(method)
    return METHOD_ORDER[index + 1] if index + 1 < len(METHOD_ORDER) else None


def _positive_int(value: Any, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MethodAttemptError(f"{field} must be a positive integer.")
    if maximum is not None and value > maximum:
        raise MethodAttemptError(f"{field} must not exceed {maximum}.")
    return value


def _json_object(value: Any, field: str) -> tuple[dict[str, Any], str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise MethodAttemptError(f"{field} must be a JSON object.")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MethodAttemptError(f"{field} must contain finite JSON values.") from exc
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
        raise MethodAttemptError(f"{field} exceeds the {_MAX_JSON_BYTES}-byte limit.")
    return value, encoded


def append_attempt_event(
    path_topic_id: int,
    method: str,
    attempt_number: int,
    status: str,
    *,
    event_key: str,
    job_id: int | None = None,
    external_run_ref: str | None = None,
    content_sha256: str | None = None,
    score: float | None = None,
    diagnosis: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one immutable transition, safely replaying an identical event key."""
    _positive_int(path_topic_id, "path_topic_id")
    _positive_int(attempt_number, "attempt_number", maximum=3)
    if method not in METHOD_ORDER:
        raise MethodAttemptError("Unsupported learning method.")
    if status not in METHOD_STATUSES:
        raise MethodAttemptError("Unsupported method-attempt status.")
    if not isinstance(event_key, str) or not 1 <= len(event_key.strip()) <= 200:
        raise MethodAttemptError("event_key must contain 1–200 characters.")
    event_key = event_key.strip()
    if job_id is not None:
        _positive_int(job_id, "job_id")
    if external_run_ref is not None and (
        not isinstance(external_run_ref, str) or not external_run_ref.strip()
        or len(external_run_ref.strip()) > 300
    ):
        raise MethodAttemptError("external_run_ref must contain 1–300 characters.")
    if content_sha256 is not None and (
        not isinstance(content_sha256, str) or not _SHA256.fullmatch(content_sha256)
    ):
        raise MethodAttemptError("content_sha256 must be a lowercase SHA-256 digest.")
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, (int, float))
        or not 0 <= score <= 100
    ):
        raise MethodAttemptError("score must be between 0 and 100.")
    diagnosis, diagnosis_json = _json_object(diagnosis, "diagnosis")
    evidence, evidence_json = _json_object(evidence, "evidence")
    payload = {
        "path_topic_id": path_topic_id,
        "method": method,
        "attempt_number": attempt_number,
        "status": status,
        "job_id": job_id,
        "external_run_ref": external_run_ref.strip() if external_run_ref else None,
        "content_sha256": content_sha256,
        "score": float(score) if score is not None else None,
        "diagnosis": diagnosis,
        "evidence": evidence,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    payload_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    occurred_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    with learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM learning_topic_method_attempt_events WHERE event_key=?",
            (event_key,),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] != payload_sha256:
                raise MethodAttemptError("event_key was reused with different event data.")
            result = dict(existing)
            result["idempotent_replay"] = True
            return result

        latest = connection.execute(
            """SELECT status, event_sequence
               FROM learning_topic_method_attempt_events
               WHERE path_topic_id=? AND method=? AND attempt_number=?
               ORDER BY event_sequence DESC LIMIT 1""",
            (path_topic_id, method, attempt_number),
        ).fetchone()
        previous_status = str(latest["status"]) if latest is not None else None
        allowed = (
            {"queued"} if previous_status is None
            else {"running", "failed", "unavailable"} if previous_status == "queued"
            else {"passed", "failed", "unavailable", "inconclusive"} if previous_status == "running"
            else set()
        )
        if status not in allowed:
            raise MethodAttemptError(
                f"Invalid method-attempt transition: {previous_status or 'new'} -> {status}."
            )
        sequence = int(latest["event_sequence"]) + 1 if latest is not None else 1
        cursor = connection.execute(
            """INSERT INTO learning_topic_method_attempt_events(
                   path_topic_id, method, attempt_number, event_sequence, status,
                   event_key, payload_sha256, job_id, external_run_ref,
                   content_sha256, score, diagnosis_json, evidence_json, occurred_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                path_topic_id, method, attempt_number, sequence, status, event_key,
                payload_sha256, job_id, payload["external_run_ref"], content_sha256,
                payload["score"], diagnosis_json, evidence_json, occurred_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM learning_topic_method_attempt_events WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
    result = dict(row)
    result["idempotent_replay"] = False
    return result


def list_attempt_events(path_topic_id: int) -> list[dict[str, Any]]:
    """Read immutable method events in their durable insertion order."""
    _positive_int(path_topic_id, "path_topic_id")
    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM learning_topic_method_attempt_events
               WHERE path_topic_id=? ORDER BY id""",
            (path_topic_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def latest_attempt_event(
    path_topic_id: int, method: str, attempt_number: int
) -> dict[str, Any] | None:
    """Return the latest durable state event for one method attempt."""
    _positive_int(path_topic_id, "path_topic_id")
    _positive_int(attempt_number, "attempt_number", maximum=3)
    if method not in METHOD_ORDER:
        raise MethodAttemptError("Unsupported learning method.")
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT * FROM learning_topic_method_attempt_events
               WHERE path_topic_id=? AND method=? AND attempt_number=?
               ORDER BY event_sequence DESC LIMIT 1""",
            (path_topic_id, method, attempt_number),
        ).fetchone()
    return dict(row) if row is not None else None
