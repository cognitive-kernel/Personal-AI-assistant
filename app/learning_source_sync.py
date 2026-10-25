"""Auditable detection of changes in source snapshots used to generate lessons.

Checks are bounded and read-only with respect to lessons: a changed source never
silently rewrites a lesson, replaces a baseline, or changes learner progress.
A reviewer must explicitly record how a detected change should be handled.
"""
from __future__ import annotations

import hashlib
from typing import Any

from . import web_sources
from .db import init_databases, learning_connection

MAX_SOURCES_PER_CHECK = 20
MAX_SNAPSHOT_CHARS = 6000
_REVIEW_DECISIONS = {"keep_lesson", "regenerate_lesson", "needs_manual_resolution"}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def capture_source_snapshot(
    connection,
    *,
    job_id: int,
    evidence_id: int,
    source_ref: str,
    source_title: str,
    snapshot_text: str,
) -> None:
    """Persist the first captured text hash; later lesson runs must not overwrite it."""
    if not isinstance(source_ref, str) or not source_ref.startswith("https://") or len(source_ref) > 2048:
        raise ValueError("Source snapshot requires a bounded HTTPS URL.")
    if not isinstance(snapshot_text, str) or not snapshot_text.strip():
        raise ValueError("Source snapshot text must not be empty.")
    text = snapshot_text.strip()[:MAX_SNAPSHOT_CHARS]
    connection.execute(
        """INSERT INTO learning_source_snapshots(
               job_id, evidence_id, source_ref, source_title, snapshot_sha256, snapshot_chars
           ) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(evidence_id) DO NOTHING""",
        (job_id, evidence_id, source_ref, source_title[:300], _sha256(text), len(text)),
    )


def check_sources(*, limit: int = 10) -> dict[str, Any]:
    """Compare bounded stored snapshots with freshly fetched public pages.

    Failures are recorded as unavailable, not as evidence that a source changed.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SOURCES_PER_CHECK:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SOURCES_PER_CHECK}.")
    init_databases()
    with learning_connection() as connection:
        snapshots = [
            dict(row) for row in connection.execute(
                """SELECT s.id, s.job_id, s.evidence_id, s.source_ref, s.source_title,
                          s.snapshot_sha256, s.snapshot_chars
                   FROM learning_source_snapshots AS s
                   JOIN learning_jobs AS j ON j.id=s.job_id
                   WHERE j.status='completed'
                   ORDER BY s.id
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        ]

    results: list[dict[str, Any]] = []
    for snapshot in snapshots:
        source_ref = str(snapshot["source_ref"])
        try:
            document = web_sources.fetch_public_document(
                source_ref,
                str(snapshot["source_title"] or "learning source"),
                max_chars=max(500, min(MAX_SNAPSHOT_CHARS, int(snapshot["snapshot_chars"]))),
            )
            current_text = document.text.strip()[: int(snapshot["snapshot_chars"])]
            if not current_text:
                raise ValueError("Fetched source contained no text.")
            observed_hash = _sha256(current_text)
            outcome = "unchanged" if observed_hash == snapshot["snapshot_sha256"] else "changed"
            detail = "Snapshot text hash matches." if outcome == "unchanged" else "Live source text differs from the captured lesson source."
            observed_chars = len(current_text)
        except Exception as exc:
            observed_hash = ""
            observed_chars = 0
            observed_text = None
            outcome = "unavailable"
            detail = "Safe source fetch failed (" + type(exc).__name__ + "); no change conclusion was made."

        if outcome != "unavailable":
            observed_text = current_text

        with learning_connection() as connection:
            connection.execute(
                """INSERT INTO learning_source_sync_events(
                       snapshot_id, observed_sha256, observed_chars, outcome, observed_text, detail
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(snapshot_id, observed_sha256, outcome) DO NOTHING""",
                (snapshot["id"], observed_hash, observed_chars, outcome, observed_text, detail),
            )
            event = connection.execute(
                """SELECT id, snapshot_id, observed_sha256, observed_chars, outcome,
                          observed_text, detail, checked_at, review_decision, review_note, reviewed_at
                   FROM learning_source_sync_events
                   WHERE snapshot_id=? AND observed_sha256=? AND outcome=?
                   ORDER BY id DESC LIMIT 1""",
                (snapshot["id"], observed_hash, outcome),
            ).fetchone()
        event_data = dict(event) if event else None
        if event_data is not None:
            event_data.pop("observed_text", None)
        results.append({
            "snapshot_id": int(snapshot["id"]),
            "job_id": int(snapshot["job_id"]),
            "evidence_id": int(snapshot["evidence_id"]),
            "source_ref": source_ref,
            "outcome": outcome,
            "event": event_data,
        })
    return {
        "checked": len(results),
        "changed": sum(item["outcome"] == "changed" for item in results),
        "unavailable": sum(item["outcome"] == "unavailable" for item in results),
        "results": results,
        "lessons_modified": False,
    }


def record_review_decision(
    event_id: int,
    *,
    decision: str,
    note: str,
) -> dict[str, Any]:
    """Audit a human decision for a source change without silently applying it."""
    if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
        raise ValueError("event_id must be a positive integer.")
    if not isinstance(decision, str) or decision not in _REVIEW_DECISIONS:
        raise ValueError("decision must be keep_lesson, regenerate_lesson, or needs_manual_resolution.")
    if not isinstance(note, str) or not 20 <= len(note.strip()) <= 1000:
        raise ValueError("note must contain 20–1000 non-whitespace characters.")
    init_databases()
    with learning_connection() as connection:
        row = connection.execute(
            "SELECT id, outcome, review_decision FROM learning_source_sync_events WHERE id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        if row["outcome"] != "changed":
            raise ValueError("Only a detected source change can receive a review decision.")
        if row["review_decision"] != "pending":
            raise ValueError("A source-change decision is immutable; create a new check to record a later decision.")
        connection.execute(
            """UPDATE learning_source_sync_events
               SET review_decision=?, review_note=?, reviewed_at=CURRENT_TIMESTAMP
               WHERE id=? AND review_decision='pending'""",
            (decision, note.strip(), event_id),
        )
        updated = connection.execute(
            """SELECT id, snapshot_id, observed_sha256, outcome, detail, checked_at,
                      review_decision, review_note, reviewed_at
               FROM learning_source_sync_events WHERE id=?""",
            (event_id,),
        ).fetchone()
    return dict(updated)
