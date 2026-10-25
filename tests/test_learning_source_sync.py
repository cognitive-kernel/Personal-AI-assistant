from types import SimpleNamespace

import pytest

from app import db, learning, learning_source_sync
from app.db import learning_connection


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES (?, 'completed', 'web_retrieved')",
            ("Source sync test",),
        ).lastrowid
        evidence_id = connection.execute(
            """INSERT INTO learning_evidence(job_id, evidence_type, source_ref, content_hash, note)
               VALUES (?, 'web_page', ?, ?, ?)""",
            (job_id, "https://docs.python.org/3/tutorial/index.html", "a" * 64, "captured source"),
        ).lastrowid
        learning_source_sync.capture_source_snapshot(
            connection,
            job_id=int(job_id),
            evidence_id=int(evidence_id),
            source_ref="https://docs.python.org/3/tutorial/index.html",
            source_title="Python tutorial",
            snapshot_text="The Python tutorial describes language basics and practical examples.",
        )
    return int(job_id), int(evidence_id)


def test_source_sync_detects_unchanged_snapshot_without_mutating_lesson(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    text = "The Python tutorial describes language basics and practical examples."
    monkeypatch.setattr(
        learning_source_sync.web_sources,
        "fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(text=text, title="Python tutorial"),
    )

    result = learning_source_sync.check_sources(limit=5)

    assert result["checked"] == 1
    assert result["changed"] == 0
    assert result["unavailable"] == 0
    assert result["lessons_modified"] is False
    assert result["results"][0]["outcome"] == "unchanged"
    assert result["results"][0]["event"]["review_decision"] == "pending"


def test_source_sync_records_change_and_requires_immutable_human_decision(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        learning_source_sync.web_sources,
        "fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(
            text="The Python tutorial has changed to describe a different API behavior."
        ),
    )

    result = learning_source_sync.check_sources(limit=1)
    event_id = result["results"][0]["event"]["id"]

    assert result["changed"] == 1
    assert result["results"][0]["outcome"] == "changed"
    reviewed = learning_source_sync.record_review_decision(
        event_id,
        decision="regenerate_lesson",
        note="The changed source affects a key example; regenerate the lesson before reuse.",
    )
    assert reviewed["review_decision"] == "regenerate_lesson"
    assert reviewed["reviewed_at"]

    queued = learning_source_sync.queue_lesson_regeneration(event_id)
    retried = learning_source_sync.queue_lesson_regeneration(event_id)
    assert queued["job_id"] == retried["job_id"]
    assert queued["task_id"] == retried["task_id"]
    assert queued["lesson_modified_in_place"] is False
    regenerated_job = learning.get_job(queued["job_id"])
    assert regenerated_job["source_kind"] == "web_retrieved"
    assert any(item["evidence_type"] == "web_page" for item in regenerated_job["evidence"])

    with pytest.raises(ValueError, match="immutable"):
        learning_source_sync.record_review_decision(
            event_id,
            decision="keep_lesson",
            note="A second decision must not overwrite the first audited decision.",
        )


def test_source_sync_fetch_failure_is_not_misreported_as_a_change(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(learning_source_sync.web_sources, "fetch_public_document", fail)
    result = learning_source_sync.check_sources(limit=1)

    assert result["unavailable"] == 1
    assert result["changed"] == 0
    assert result["results"][0]["outcome"] == "unavailable"
    assert result["results"][0]["event"]["observed_sha256"] == ""
    with pytest.raises(ValueError, match="Only a detected source change"):
        learning_source_sync.record_review_decision(
            result["results"][0]["event"]["id"],
            decision="keep_lesson",
            note="Network failure does not prove that the stored source has changed.",
        )


@pytest.mark.parametrize("limit", [0, 21, True, 1.5])
def test_source_sync_bounds_requested_work(tmp_path, monkeypatch, limit):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="limit must be"):
        learning_source_sync.check_sources(limit=limit)
