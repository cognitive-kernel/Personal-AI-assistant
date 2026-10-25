from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import cloud_resources, curriculum_evolution, db, learning_source_sync
from app.main import app
from app.db import learning_connection


def _setup(tmp_path, monkeypatch, *, score=55):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()
    with learning_connection() as connection:
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('evolution-test', 'Evolution test')"
        ).lastrowid
        topic_id = connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status, latest_score)
               VALUES (?, 'Basics', 1, 'completed', ?)""",
            (path_id, score),
        ).lastrowid
        next_topic_id = connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status)
               VALUES (?, 'Next topic', 2, 'pending')""",
            (path_id,),
        ).lastrowid
    return int(path_id), int(topic_id), int(next_topic_id)


def test_low_score_creates_idempotent_reinforcement_proposal_and_acceptance_inserts_topic(tmp_path, monkeypatch):
    path_id, topic_id, next_topic_id = _setup(tmp_path, monkeypatch, score=64)

    generated = curriculum_evolution.generate_proposals(path_id=path_id)
    assert generated["created"] == 1
    assert generated["autonomous_mutation"] is False
    proposal = generated["proposals"][0]
    assert proposal["proposal_type"] == "reinforce_topic"
    assert proposal["evidence"]["latest_score"] == 64
    again = curriculum_evolution.generate_proposals(path_id=path_id)
    assert again["created"] == 0
    assert len(again["proposals"]) == 1

    result = curriculum_evolution.review_proposal(
        proposal["id"],
        decision="accept",
        note="The low score indicates the learner should review this concept before proceeding.",
    )
    assert result["proposal"]["status"] == "accepted"
    assert result["proposal"]["applied_topic_id"]
    with learning_connection() as connection:
        rows = connection.execute(
            "SELECT id, topic, position, status, latest_score FROM learning_path_topics WHERE path_id=? ORDER BY position",
            (path_id,),
        ).fetchall()
    assert [row["topic"] for row in rows] == [
        "Basics", "مرور تقویتی: Basics", "Next topic"
    ]
    assert rows[0]["id"] == topic_id
    assert rows[0]["latest_score"] == 64
    assert rows[2]["id"] == next_topic_id


def test_rejected_reinforcement_proposal_is_not_generated_again(tmp_path, monkeypatch):
    path_id, _, _ = _setup(tmp_path, monkeypatch, score=30)
    proposal = curriculum_evolution.generate_proposals(path_id=path_id)["proposals"][0]
    curriculum_evolution.review_proposal(
        proposal["id"],
        decision="reject",
        note="The learner's low score came from an ambiguous question, not a missing concept.",
    )
    again = curriculum_evolution.generate_proposals(path_id=path_id)
    assert again["created"] == 0
    assert len(again["proposals"]) == 1
    assert again["proposals"][0]["status"] == "rejected"


def test_changed_source_proposal_acceptance_queues_reviewed_lesson_refresh(tmp_path, monkeypatch):
    path_id, topic_id, _ = _setup(tmp_path, monkeypatch, score=95)
    source_ref = "https://docs.python.org/3/tutorial/index.html"
    original = "The original source excerpt describes stable Python tutorial behavior."
    changed = "The updated source excerpt describes a changed Python tutorial behavior."
    with learning_connection() as connection:
        job_id = connection.execute(
            """INSERT INTO learning_jobs(topic, status, source_kind, path_topic_id)
               VALUES ('Basics', 'completed', 'web_retrieved', ?)""",
            (topic_id,),
        ).lastrowid
        evidence_id = connection.execute(
            """INSERT INTO learning_evidence(job_id, evidence_type, source_ref, content_hash, note)
               VALUES (?, 'web_page', ?, ?, 'source captured for curriculum evolution')""",
            (job_id, source_ref, "a" * 64),
        ).lastrowid
        learning_source_sync.capture_source_snapshot(
            connection,
            job_id=int(job_id),
            evidence_id=int(evidence_id),
            source_ref=source_ref,
            source_title="Python tutorial",
            snapshot_text=original,
        )
    monkeypatch.setattr(
        learning_source_sync.web_sources,
        "fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(text=changed),
    )
    sync = learning_source_sync.check_sources(limit=1)
    assert sync["changed"] == 1

    generated = curriculum_evolution.generate_proposals(path_id=path_id)
    refresh = next(item for item in generated["proposals"] if item["proposal_type"] == "refresh_lesson")
    accepted = curriculum_evolution.review_proposal(
        refresh["id"],
        decision="accept",
        note="The source changed and should be reviewed before a replacement lesson is generated.",
    )

    assert accepted["proposal"]["status"] == "accepted"
    assert accepted["regeneration"]["job_id"]
    with learning_connection() as connection:
        event = connection.execute(
            "SELECT review_decision, regeneration_job_id FROM learning_source_sync_events WHERE id=?",
            (sync["results"][0]["event"]["id"],),
        ).fetchone()
        replacement = connection.execute(
            "SELECT status, source_kind, path_topic_id FROM learning_jobs WHERE id=?",
            (accepted["regeneration"]["job_id"],),
        ).fetchone()
    assert event["review_decision"] == "regenerate_lesson"
    assert event["regeneration_job_id"] == accepted["regeneration"]["job_id"]
    assert replacement["status"] == "queued"
    assert replacement["source_kind"] == "web_retrieved"
    assert replacement["path_topic_id"] == topic_id


def test_invalid_curriculum_proposal_requests_fail_closed(tmp_path, monkeypatch):
    path_id, _, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="limit"):
        curriculum_evolution.list_proposals(path_id=path_id, limit=101)
    with pytest.raises(KeyError):
        curriculum_evolution.generate_proposals(path_id=path_id + 999)
    with pytest.raises(ValueError, match="decision"):
        curriculum_evolution.review_proposal(1, decision="maybe", note="This note is long enough for review.")



def test_curriculum_proposal_api_is_loopback_only_and_requires_explicit_review(tmp_path, monkeypatch):
    path_id, _, _ = _setup(tmp_path, monkeypatch, score=61)
    monkeypatch.setattr(
        cloud_resources, "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled"},
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        generated = client.post(
            f"/api/learning/paths/{path_id}/curriculum-proposals/generate"
        )
        assert generated.status_code == 200
        proposal = generated.json()["proposals"][0]
        assert proposal["status"] == "pending"

        listed = client.get(f"/api/learning/paths/{path_id}/curriculum-proposals")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        reviewed = client.post(
            f"/api/learning/curriculum-proposals/{proposal['id']}/review",
            json={
                "decision": "accept",
                "note": "The saved assessment score supports adding a reinforcement topic before the next lesson.",
            },
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["proposal"]["status"] == "accepted"

    with TestClient(app, client=("203.0.113.10", 12345)) as client:
        blocked = client.get(f"/api/learning/paths/{path_id}/curriculum-proposals")
        assert blocked.status_code == 403



def test_learning_ui_exposes_curriculum_proposal_review_controls():
    from pathlib import Path

    page = (Path(__file__).parents[1] / "app" / "static" / "learning.js").read_text(
        encoding="utf-8"
    )
    assert "تولید پیشنهادهای تطبیق مسیر" in page
    assert '"/api/learning/paths/" + path.id + "/curriculum-proposals/generate"' in page
    assert '"/api/learning/curriculum-proposals/" + proposal.id + "/review"' in page
    assert "حداقل ۲۰ نویسه" in page
