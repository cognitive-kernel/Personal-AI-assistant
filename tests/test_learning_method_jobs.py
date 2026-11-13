import concurrent.futures

import pytest

from app import db, learning


@pytest.fixture
def method_job_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('method-jobs', 'Method jobs')"
        ).lastrowid)
        topic_id = int(connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status)
               VALUES (?, 'Research adapters', 1, 'in_progress')""",
            (path_id,),
        ).lastrowid)
    return topic_id


def test_method_job_creation_is_idempotent_and_versioned(method_job_store):
    topic_id = method_job_store
    first = learning.create_job(
        "Research adapters",
        source_text="Official source material for this method attempt.",
        path_topic_id=topic_id,
        learning_method="topic_learning",
        method_attempt_key=f"topic:{topic_id}:topic_learning:1",
    )
    replay = learning.create_job(
        "Research adapters",
        source_text="Official source material for this method attempt.",
        path_topic_id=topic_id,
        learning_method="topic_learning",
        method_attempt_key=f"topic:{topic_id}:topic_learning:1",
    )

    assert first["id"] == replay["id"]
    assert first["learning_method"] == "topic_learning"
    assert first["source_kind"] == "researched_topic_course"
    with db.learning_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE method_attempt_key=?",
            (f"topic:{topic_id}:topic_learning:1",),
        ).fetchone()[0]
        evidence = connection.execute(
            "SELECT evidence_type, source_ref FROM learning_evidence WHERE job_id=?",
            (first["id"],),
        ).fetchone()
    assert count == 1
    assert evidence["evidence_type"] == "research_context"
    assert evidence["source_ref"] == "learning-path-research"


def test_method_job_key_cannot_be_reused_for_different_content(method_job_store):
    topic_id = method_job_store
    learning.create_job(
        "Research adapters",
        source_text="First source bundle.",
        path_topic_id=topic_id,
        learning_method="topic_learning",
        method_attempt_key=f"topic:{topic_id}:topic_learning:1",
    )
    with pytest.raises(ValueError, match="different source text"):
        learning.create_job(
            "Research adapters",
            source_text="A different source bundle.",
            path_topic_id=topic_id,
            learning_method="topic_learning",
            method_attempt_key=f"topic:{topic_id}:topic_learning:1",
        )


def test_concurrent_method_job_creation_creates_one_job(method_job_store):
    topic_id = method_job_store
    key = f"topic:{topic_id}:basic_learning:1"

    def create():
        return learning.create_job(
            "Research adapters",
            path_topic_id=topic_id,
            learning_method="basic_learning",
            method_attempt_key=key,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _index: create(), range(8)))

    assert len({job["id"] for job in jobs}) == 1
    with db.learning_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM learning_jobs WHERE method_attempt_key=?",
            (key,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"learning_method": "experience_system"},
        {"method_attempt_key": ""},
        {"method_attempt_key": "not-allowed-without-topic", "path_topic_id": None},
        {"method_attempt_key": "not-allowed-with-correction", "correction_request_key": "correction"},
    ],
)
def test_method_job_contract_rejects_invalid_identity(method_job_store, kwargs):
    values = {"topic": "Research adapters", "path_topic_id": method_job_store}
    values.update(kwargs)
    with pytest.raises(ValueError):
        learning.create_job(**values)
