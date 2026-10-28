"""Auditable detection of changes in source snapshots used to generate lessons.

Checks are bounded and read-only with respect to lessons: a changed source never
silently rewrites a lesson, replaces a baseline, or changes learner progress.
A reviewer must explicitly record how a detected change should be handled.
"""
from __future__ import annotations

import hashlib
import re
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


def _extract_stored_source(source_text: str, source_ref: str) -> tuple[str, str] | None:
    pattern = re.compile(
        r"Source title:\s*(?P<title>[^\n]{1,300})\n"
        r"Source URL:\s*(?P<url>https://[^\s]+)\n"
        r"Content:\n(?P<content>.*?)(?=\n\nSource title:|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(source_text[:100000]):
        if match.group("url").strip() == source_ref:
            title = match.group("title").strip()[:300]
            text = match.group("content").strip()[:MAX_SNAPSHOT_CHARS]
            return (title, text) if text else None
    return None


def _backfill_legacy_snapshots(limit: int) -> int:
    """Create first-seen baselines from stored lesson excerpts without rewriting them."""
    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT j.id AS job_id, e.id AS evidence_id, e.source_ref, st.content
               FROM learning_jobs AS j
               JOIN learning_source_text AS st ON st.job_id=j.id
               JOIN learning_evidence AS e ON e.job_id=j.id
               LEFT JOIN learning_source_snapshots AS s ON s.evidence_id=e.id
               WHERE j.status='completed' AND e.evidence_type='web_page'
                 AND e.source_ref LIKE 'https://%' AND s.id IS NULL
               ORDER BY j.id, e.id LIMIT ?""",
            (limit,),
        ).fetchall()
        created = 0
        for row in rows:
            extracted = _extract_stored_source(str(row["content"]), str(row["source_ref"]))
            if extracted is None:
                continue
            title, text = extracted
            capture_source_snapshot(
                connection,
                job_id=int(row["job_id"]),
                evidence_id=int(row["evidence_id"]),
                source_ref=str(row["source_ref"]),
                source_title=title,
                snapshot_text=text,
            )
            created += 1
        return created


def check_sources(*, limit: int = 10) -> dict[str, Any]:
    """Compare bounded stored snapshots with freshly fetched public pages.

    Failures are recorded as unavailable, not as evidence that a source changed.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SOURCES_PER_CHECK:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SOURCES_PER_CHECK}.")
    init_databases()
    baselines_created = _backfill_legacy_snapshots(limit)
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
            latest = connection.execute(
                """SELECT observed_sha256, outcome FROM learning_source_sync_events
                   WHERE snapshot_id=? ORDER BY id DESC LIMIT 1""",
                (snapshot["id"],),
            ).fetchone()
            if latest is None or latest["observed_sha256"] != observed_hash or latest["outcome"] != outcome:
                connection.execute(
                    """INSERT INTO learning_source_sync_events(
                           snapshot_id, observed_sha256, observed_chars, outcome, observed_text, detail
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (snapshot["id"], observed_hash, observed_chars, outcome, observed_text, detail),
                )
            event = connection.execute(
                """SELECT id, snapshot_id, observed_sha256, observed_chars, outcome,
                          observed_text, detail, checked_at, review_decision, review_note, reviewed_at
                   FROM learning_source_sync_events
                   WHERE snapshot_id=? ORDER BY id DESC LIMIT 1""",
                (snapshot["id"],),
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
        "baselines_created": baselines_created,
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
            "SELECT id, outcome, review_decision, observed_text FROM learning_source_sync_events WHERE id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        if row["outcome"] != "changed":
            raise ValueError("Only a detected source change can receive a review decision.")
        if row["review_decision"] != "pending":
            raise ValueError("A source-change decision is immutable; create a new check to record a later decision.")
        if decision == "regenerate_lesson" and not str(row["observed_text"] or "").strip():
            raise ValueError("The changed source snapshot is unavailable; the lesson cannot be regenerated from it.")
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



def queue_lesson_regeneration(event_id: int) -> dict[str, Any]:
    """Queue a new lesson version from a human-approved changed source snapshot.

    The previous lesson is preserved. The decision event stores the resulting job ID
    before queue submission, so a retry can reuse the same job rather than duplicate it.
    """
    if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
        raise ValueError("event_id must be a positive integer.")
    init_databases()
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT e.id, e.outcome, e.review_decision, e.observed_text,
                      e.regeneration_job_id, s.source_ref, s.source_title,
                      j.topic, j.path_topic_id
               FROM learning_source_sync_events AS e
               JOIN learning_source_snapshots AS s ON s.id=e.snapshot_id
               JOIN learning_jobs AS j ON j.id=s.job_id
               WHERE e.id=?""",
            (event_id,),
        ).fetchone()
    if row is None:
        raise KeyError(event_id)
    if row["outcome"] != "changed" or row["review_decision"] != "regenerate_lesson":
        raise ValueError("Only a changed source with an explicit regenerate_lesson decision can be queued.")
    observed_text = str(row["observed_text"] or "").strip()
    if not observed_text:
        raise ValueError("The changed source snapshot is unavailable.")
    job_id = int(row["regeneration_job_id"]) if row["regeneration_job_id"] is not None else None

    from . import learning
    from .learning_tasks import enqueue_learning_task

    if job_id is None:
        path_topic_id = int(row["path_topic_id"]) if row["path_topic_id"] is not None else None
        if path_topic_id is not None:
            with learning_connection() as connection:
                active = connection.execute(
                    """SELECT id FROM learning_jobs
                       WHERE path_topic_id=? AND status IN ('queued', 'running')
                       ORDER BY id DESC LIMIT 1""",
                    (path_topic_id,),
                ).fetchone()
            if active is not None:
                raise ValueError("An active lesson job already exists for this path topic; resolve it before regeneration.")

        source_ref = str(row["source_ref"])
        source_title = str(row["source_title"] or "Updated learning source")[:300]
        source_text = (
            f"Source title: {source_title}\n"
            f"Source URL: {source_ref}\n"
            f"Content:\n{observed_text[:MAX_SNAPSHOT_CHARS]}"
        )
        job = learning.create_job(
            str(row["topic"]),
            source_text=source_text,
            path_topic_id=path_topic_id,
        )
        job_id = int(job["id"])
        digest = _sha256(source_text)
        with learning_connection() as connection:
            connection.execute(
                """UPDATE learning_jobs SET source_kind='web_retrieved'
                   WHERE id=? AND status='queued'""",
                (job_id,),
            )
            connection.execute(
                "DELETE FROM learning_evidence WHERE job_id=? AND evidence_type='user_text' AND source_ref='user-provided'",
                (job_id,),
            )
            connection.execute(
                """INSERT INTO learning_evidence(
                       job_id, evidence_type, source_ref, content_hash, note
                   ) VALUES (?, 'web_page', ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                (job_id, source_ref, digest, "Human-approved changed public source snapshot; content still requires independent review."),
            )
            evidence = connection.execute(
                """SELECT id FROM learning_evidence
                   WHERE job_id=? AND evidence_type='web_page' AND source_ref=? AND content_hash=?
                   ORDER BY id LIMIT 1""",
                (job_id, source_ref, digest),
            ).fetchone()
            if evidence is None:
                raise RuntimeError("Regeneration source evidence could not be recorded.")
            capture_source_snapshot(
                connection,
                job_id=job_id,
                evidence_id=int(evidence["id"]),
                source_ref=source_ref,
                source_title=source_title,
                snapshot_text=observed_text[:MAX_SNAPSHOT_CHARS],
            )
            connection.execute(
                """UPDATE learning_source_sync_events SET regeneration_job_id=?
                   WHERE id=? AND regeneration_job_id IS NULL""",
                (job_id, event_id),
            )
            linked = connection.execute(
                "SELECT regeneration_job_id FROM learning_source_sync_events WHERE id=?",
                (event_id,),
            ).fetchone()
            if linked is None or int(linked["regeneration_job_id"] or 0) != job_id:
                raise RuntimeError("Regeneration job could not be linked to the review event.")

    task = enqueue_learning_task(job_id)
    return {
        "event_id": event_id,
        "job_id": job_id,
        "task_id": task.get("id"),
        "task_status": task.get("status"),
        "lesson_modified_in_place": False,
    }
