import json

import pytest

from app import db, learning


def _ready_job(tmp_path, monkeypatch, *, with_path=False, rubric=None):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    topic_id = None
    path_id = None
    if with_path:
        with db.learning_connection() as conn:
            path_id = int(conn.execute(
                "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
                ("rubric-test-path", "Rubric test path"),
            ).lastrowid)
            topic_id = int(conn.execute(
                "INSERT INTO learning_path_topics(path_id, topic, position) VALUES (?, ?, ?)",
                (path_id, "Python lists", 1),
            ).lastrowid)
    job = learning.create_job("Python lists", path_topic_id=topic_id)
    assessment = {
        "learning_outcomes": ["Explain list mutability"],
        "questions": ["What does mutable mean?", "What does an index select?", "How can an item be replaced?"],
        "answer_key": [
            "Items can be changed after creation.",
            "An index selects a position.",
            "Assign a new value to the item.",
        ],
        "independently_verified": False,
    }
    if rubric is not None:
        assessment["rubric"] = rubric
        assessment["rubric_version"] = "concept-coverage-v1"
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE learning_jobs SET status='completed', lesson=?, assessment_json=? WHERE id=?",
            ("Python lists are mutable.", json.dumps(assessment, ensure_ascii=False), job["id"]),
        )
    return job["id"], path_id, topic_id


def _rubric():
    return [
        {"criteria": [
            {"id": "mutable", "weight": 1, "accepted_phrases": ["changed after creation", "modified after creation"]},
            {"id": "in-place", "weight": 1, "accepted_phrases": ["items can be changed", "items can be modified"]},
        ]},
        {"criteria": [
            {"id": "index", "weight": 1, "accepted_phrases": ["index selects a position", "position selected by index"]},
            {"id": "position", "weight": 1, "accepted_phrases": ["selects a position", "selects an item position"]},
        ]},
        {"criteria": [
            {"id": "assignment", "weight": 1, "accepted_phrases": ["assign a new value", "replace the item by assignment"]},
            {"id": "target", "weight": 1, "accepted_phrases": ["to the item", "to that item"]},
        ]},
    ]


def test_rubric_score_is_persisted_and_idempotent(tmp_path, monkeypatch):
    job_id, _, _ = _ready_job(tmp_path, monkeypatch, rubric=_rubric())
    key = "assessment-test-idempotency-001"
    answers = [
        "Items can be changed after creation.",
        "An index selects a position.",
        "Assign a new value to the item.",
    ]

    first = learning.submit_assessment(job_id, answers, key)
    replay = learning.submit_assessment(job_id, answers, key)

    assert first["score"] == 100
    assert first["scoring_method"] == "rubric_concept_coverage_v1"
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["attempt_id"] == first["attempt_id"]
    assert replay["per_question"] == first["per_question"]
    with pytest.raises(ValueError, match="different answers"):
        learning.submit_assessment(job_id, ["different", *answers[1:]], key)


def test_rubric_and_answer_cues_are_never_exposed_in_job_payload(tmp_path, monkeypatch):
    job_id, _, _ = _ready_job(tmp_path, monkeypatch, rubric=_rubric())

    before = learning.get_job(job_id)
    assert "answer_key" not in before["assessment"]
    assert "rubric" not in before["assessment"]
    assert "rubric_version" not in before["assessment"]

    learning.submit_assessment(
        job_id,
        ["Items can be changed after creation.", "An index selects a position.", "Assign a new value to the item."],
        "assessment-test-idempotency-002",
    )
    after = learning.get_job(job_id)
    assert "answer_key" in after["assessment"]
    assert "rubric" not in after["assessment"]
    assert "rubric_version" not in after["assessment"]


def test_rubric_threshold_cannot_be_bypassed_by_manual_completion(tmp_path, monkeypatch):
    job_id, path_id, topic_id = _ready_job(
        tmp_path, monkeypatch, with_path=True, rubric=_rubric()
    )
    result = learning.submit_assessment(
        job_id,
        ["changed after creation", "index selects a position", "unrelated answer"],
        "assessment-test-threshold-001",
    )
    assert result["score"] < 80
    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT status, latest_score, latest_score_kind FROM learning_path_topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    assert row["status"] == "in_progress"
    assert row["latest_score_kind"] == "learner_rubric"
    with pytest.raises(ValueError, match="at least 80"):
        learning.update_path_topic(path_id, topic_id, "completed")
    with pytest.raises(ValueError, match="cannot be manually overridden"):
        learning.update_path_topic(path_id, topic_id, "in_progress", 100)


def test_legacy_assessment_keeps_exact_match_fallback(tmp_path, monkeypatch):
    job_id, _, _ = _ready_job(tmp_path, monkeypatch, rubric=None)
    result = learning.submit_assessment(
        job_id,
        ["List items can be modified later.", "Positions are selected by the index.", "Assignment replaces the existing item."],
        "assessment-test-legacy-001",
    )
    assert result["scoring_method"] == "normalized_exact_match"
    assert result["score"] == 0



def test_rubric_migration_adds_idempotency_and_score_provenance_columns(tmp_path, monkeypatch):
    _ready_job(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        attempt_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(assessment_attempts)")
        }
        topic_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(learning_path_topics)")
        }
        migration = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    assert {"idempotency_key", "result_details_json"} <= attempt_columns
    assert "latest_score_kind" in topic_columns
    assert migration["version"] == 38



def test_legacy_exact_match_score_cannot_be_overridden_to_bypass_completion_gate(tmp_path, monkeypatch):
    job_id, path_id, topic_id = _ready_job(
        tmp_path, monkeypatch, with_path=True, rubric=None
    )
    result = learning.submit_assessment(
        job_id,
        ["wrong answer", "wrong answer", "wrong answer"],
        "assessment-legacy-gate-0001",
    )
    assert result["score"] == 0

    with pytest.raises(ValueError, match="at least 70"):
        learning.update_path_topic(path_id, topic_id, "completed")
    with pytest.raises(ValueError, match="assessment-derived score cannot be manually overridden"):
        learning.update_path_topic(path_id, topic_id, "completed", 100)

    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT status, latest_score, latest_score_kind FROM learning_path_topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    assert row["status"] == "in_progress"
    assert row["latest_score"] == 0
    assert row["latest_score_kind"] == "legacy_exact_match"


def test_manual_score_cannot_create_assessment_completion_evidence(tmp_path, monkeypatch):
    _job_id, path_id, topic_id = _ready_job(
        tmp_path, monkeypatch, with_path=True, rubric=None
    )
    with pytest.raises(ValueError, match="recorded passing assessment"):
        learning.update_path_topic(path_id, topic_id, "completed", 100)

    with db.learning_connection() as conn:
        row = conn.execute(
            "SELECT status, latest_score, latest_score_kind FROM learning_path_topics WHERE id=?",
            (topic_id,),
        ).fetchone()
    assert row["status"] == "pending"
    assert row["latest_score"] is None
    assert row["latest_score_kind"] == "legacy_unknown"


def test_non_finite_progress_scores_are_rejected(tmp_path, monkeypatch):
    _job_id, path_id, topic_id = _ready_job(
        tmp_path, monkeypatch, with_path=True, rubric=None
    )
    for invalid_score in (float("nan"), float("inf"), float("-inf"), True):
        with pytest.raises(ValueError, match="Score must be between 0 and 100"):
            learning.update_path_topic(path_id, topic_id, "in_progress", invalid_score)
