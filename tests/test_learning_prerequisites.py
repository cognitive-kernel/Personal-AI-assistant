import pytest

from app import db, learning, learning_prerequisites


@pytest.fixture
def prerequisite_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as connection:
        path_id = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("custom-prerequisites", "Custom prerequisites"),
        ).lastrowid)
        topic_ids = []
        for position, topic in enumerate(("A foundation", "Independent branch", "Dependent topic"), start=1):
            topic_ids.append(int(connection.execute(
                """INSERT INTO learning_path_topics(path_id, topic, position, status)
                   VALUES (?, ?, ?, 'pending')""",
                (path_id, topic, position),
            ).lastrowid))
    return path_id, topic_ids


def test_custom_dag_allows_ready_branch_and_blocks_unmet_dependency(prerequisite_store):
    path_id, (foundation_id, independent_id, dependent_id) = prerequisite_store
    learning_prerequisites.set_topic_prerequisites(path_id, dependent_id, [foundation_id])

    # A later topic can start before the first topic when custom DAG mode says it is independent.
    independent_job = learning.create_job("Independent branch", path_topic_id=independent_id)
    assert independent_job["path_topic_id"] == independent_id

    with pytest.raises(ValueError, match="Prerequisite topic is not completed: A foundation"):
        learning.create_job("Dependent topic", path_topic_id=dependent_id)


def test_custom_prerequisite_dag_rejects_cycles(prerequisite_store):
    path_id, (a_id, b_id, c_id) = prerequisite_store
    learning_prerequisites.set_topic_prerequisites(path_id, b_id, [a_id])
    learning_prerequisites.set_topic_prerequisites(path_id, c_id, [b_id])

    with pytest.raises(ValueError, match="cycle"):
        learning_prerequisites.set_topic_prerequisites(path_id, a_id, [c_id])


def test_prerequisite_edges_must_stay_inside_same_path(prerequisite_store):
    path_id, (a_id, b_id, _c_id) = prerequisite_store
    with db.learning_connection() as connection:
        other_path = int(connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES (?, ?)",
            ("other-prerequisites", "Other prerequisites"),
        ).lastrowid)
        outside_id = int(connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status)
               VALUES (?, ?, 1, 'pending')""",
            (other_path, "Outside topic"),
        ).lastrowid)

    with pytest.raises(ValueError, match="same learning path"):
        learning_prerequisites.set_topic_prerequisites(path_id, b_id, [outside_id])


def test_custom_mode_with_no_dependencies_makes_topic_eligible(prerequisite_store):
    path_id, (_a_id, independent_id, _dependent_id) = prerequisite_store
    learning_prerequisites.set_topic_prerequisites(path_id, independent_id, [])
    result = learning_prerequisites.get_topic_prerequisites(path_id, independent_id)
    assert result["prerequisite_mode"] == "custom"
    assert result["prerequisites"] == []


def test_linear_mode_explicitly_blocks_later_topics(prerequisite_store):
    path_id, (_first_id, later_id, _third_id) = prerequisite_store
    learning_prerequisites.set_path_mode(path_id, "linear")
    with pytest.raises(ValueError, match="Prerequisite topic is not completed: A foundation"):
        learning.create_job("Independent branch", path_topic_id=later_id)



def test_blocked_topic_start_does_not_mutate_topic_status(prerequisite_store):
    path_id, (foundation_id, _independent_id, dependent_id) = prerequisite_store
    learning_prerequisites.set_path_mode(path_id, "custom")
    learning_prerequisites.set_topic_prerequisites(path_id, dependent_id, [foundation_id])

    with pytest.raises(ValueError, match="Prerequisite topic is not completed: A foundation"):
        learning.start_topic_learning(path_id, dependent_id)

    with db.learning_connection() as connection:
        topic = connection.execute(
            "SELECT status FROM learning_path_topics WHERE id=?", (dependent_id,)
        ).fetchone()
        jobs = connection.execute(
            "SELECT COUNT(*) AS count FROM learning_jobs WHERE path_topic_id=?", (dependent_id,)
        ).fetchone()
    assert topic["status"] == "pending"
    assert jobs["count"] == 0


def test_blocked_regeneration_preserves_existing_lesson(prerequisite_store):
    path_id, (foundation_id, _independent_id, dependent_id) = prerequisite_store
    learning_prerequisites.set_path_mode(path_id, "custom")
    old_job = learning.create_job("Dependent topic", path_topic_id=dependent_id)
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE learning_jobs SET status='completed', lesson=? WHERE id=?",
            ("Existing lesson that must not be deleted.", old_job["id"]),
        )
        connection.execute(
            "UPDATE learning_path_topics SET status='completed' WHERE id=?", (dependent_id,)
        )
    learning_prerequisites.set_topic_prerequisites(path_id, dependent_id, [foundation_id])

    with pytest.raises(ValueError, match="Prerequisite topic is not completed: A foundation"):
        learning.regenerate_topic_learning(path_id, dependent_id)

    with db.learning_connection() as connection:
        saved_job = connection.execute(
            "SELECT status, lesson FROM learning_jobs WHERE id=?", (old_job["id"],)
        ).fetchone()
        topic = connection.execute(
            "SELECT status FROM learning_path_topics WHERE id=?", (dependent_id,)
        ).fetchone()
    assert saved_job["status"] == "completed"
    assert saved_job["lesson"] == "Existing lesson that must not be deleted."
    assert topic["status"] == "completed"
