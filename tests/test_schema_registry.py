import json
import sqlite3

import pytest

from app import db, task_manager
from app.schema_migrations import MigrationError, MigrationRunner, UntrackedSchemaError, schema_fingerprint
from app.schema_registry import (
    LEARNING_MIGRATIONS,
    SETTINGS_MIGRATIONS,
    fingerprint_for_version,
    initialize_connection,
    initialize_database_file,
    restore_database_backup,
)


def test_settings_schema_can_be_created_from_versioned_migrations():
    with sqlite3.connect(":memory:") as connection:
        runner = MigrationRunner(SETTINGS_MIGRATIONS)
        assert runner.apply(connection) == 3
        assert runner.apply(connection) == 3
        columns = {row[1] for row in connection.execute("PRAGMA table_info(settings)")}
        assert {"key", "value_json", "updated_at"} <= columns
        consent_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sandbox_execution_consent)")
        }
        assert {"enabled", "policy_version", "consented_at", "revoked_at"} <= consent_columns
        assert connection.execute(
            "SELECT enabled FROM sandbox_execution_consent WHERE id=1"
        ).fetchone() == (0,)
        capture_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(experience_auto_capture_consent)")
        }
        assert {"enabled", "policy_version", "consented_at", "revoked_at"} <= capture_columns
        assert connection.execute(
            "SELECT enabled FROM experience_auto_capture_consent WHERE id=1"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_learning_schema_migrates_from_v1_without_losing_rows():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert MigrationRunner(LEARNING_MIGRATIONS[:1]).apply(connection) == 1
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('retained', 'queued', 'user_text')"
        ).lastrowid
        evidence_id = connection.execute(
            """INSERT INTO learning_evidence(
                   job_id, evidence_type, source_ref, content_hash, note
               ) VALUES (?, 'user_text', 'file://lesson.md', 'sha256:abc123', 'source retained')""",
            (job_id,),
        ).lastrowid
        connection.execute(
            "INSERT INTO learning_source_text(job_id, content) VALUES (?, ?)",
            (job_id, "original source text"),
        )
        knowledge_id = connection.execute(
            """INSERT INTO knowledge_items(
                   topic, content, source_kind, version, status, job_id
               ) VALUES ('retained', 'canonical lesson', 'user_text', 1, 'unverified', ?)""",
            (job_id,),
        ).lastrowid
        assessment_id = connection.execute(
            """INSERT INTO assessment_attempts(
                   job_id, answers_json, score, correct_count, question_count
               ) VALUES (?, '["a"]', 100, 1, 1)""",
            (job_id,),
        ).lastrowid
        review_id = connection.execute(
            """INSERT INTO lesson_reviews(
                   job_id, outcomes_json, value_rating, takeaway, issues
               ) VALUES (?, '["useful"]', 5, 'keep this insight', '')""",
            (job_id,),
        ).lastrowid
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('retained-path', 'Retained path')"
        ).lastrowid
        path_topic_id = connection.execute(
            """INSERT INTO learning_path_topics(path_id, topic, position, status)
               VALUES (?, 'retained', 0, 'completed')""",
            (path_id,),
        ).lastrowid
        task_id = connection.execute(
            "INSERT INTO task_queue(task_type, payload_json) VALUES ('learning.run_job', ?)",
            (json.dumps({"job_id": job_id}),),
        ).lastrowid
        connection.commit()

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 8
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 8

        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_jobs)")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_queue)")}
        assert {"quality_json", "path_topic_id", "lesson_review_json"} <= job_columns
        assert "dedupe_key" in task_columns
        assert connection.execute(
            "SELECT topic, status, source_kind FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("retained", "queued", "user_text")
        assert connection.execute(
            """SELECT evidence_type, source_ref, content_hash, note
               FROM learning_evidence WHERE id=?""",
            (evidence_id,),
        ).fetchone() == ("user_text", "file://lesson.md", "sha256:abc123", "source retained")
        assert connection.execute(
            "SELECT content FROM learning_source_text WHERE job_id=?", (job_id,)
        ).fetchone() == ("original source text",)
        assert connection.execute(
            "SELECT id, content, source_kind, version, status, job_id FROM knowledge_items WHERE id=?",
            (knowledge_id,),
        ).fetchone() == (knowledge_id, "canonical lesson", "user_text", 1, "unverified", job_id)
        assert connection.execute(
            "SELECT id, answers_json, score, correct_count, question_count FROM assessment_attempts WHERE id=?",
            (assessment_id,),
        ).fetchone() == (assessment_id, '["a"]', 100.0, 1, 1)
        assert connection.execute(
            "SELECT id, outcomes_json, value_rating, takeaway, issues FROM lesson_reviews WHERE id=?",
            (review_id,),
        ).fetchone() == (review_id, '["useful"]', 5, "keep this insight", "")
        assert connection.execute(
            "SELECT id, curriculum_slug, title FROM learning_paths WHERE id=?", (path_id,)
        ).fetchone() == (path_id, "retained-path", "Retained path")
        assert connection.execute(
            "SELECT id, path_id, topic, position, status FROM learning_path_topics WHERE id=?",
            (path_topic_id,),
        ).fetchone() == (path_topic_id, path_id, "retained", 0, "completed")
        assert connection.execute(
            "SELECT task_type, payload_json FROM task_queue WHERE id=?", (task_id,)
        ).fetchone() == ("learning.run_job", json.dumps({"job_id": job_id}))
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(task_queue)")}
        assert "idx_task_queue_dedupe_key" in indexes
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)]


def test_registry_exposes_reviewable_canonical_fingerprints():
    settings_fingerprint = fingerprint_for_version(SETTINGS_MIGRATIONS, 1)
    settings_v2 = fingerprint_for_version(SETTINGS_MIGRATIONS, 2)
    settings_v3 = fingerprint_for_version(SETTINGS_MIGRATIONS, 3)
    learning_v1 = fingerprint_for_version(LEARNING_MIGRATIONS, 1)
    learning_v2 = fingerprint_for_version(LEARNING_MIGRATIONS, 2)
    learning_v3 = fingerprint_for_version(LEARNING_MIGRATIONS, 3)
    learning_v4 = fingerprint_for_version(LEARNING_MIGRATIONS, 4)
    learning_v5 = fingerprint_for_version(LEARNING_MIGRATIONS, 5)
    learning_v6 = fingerprint_for_version(LEARNING_MIGRATIONS, 6)
    learning_v7 = fingerprint_for_version(LEARNING_MIGRATIONS, 7)
    learning_v8 = fingerprint_for_version(LEARNING_MIGRATIONS, 8)

    assert len(settings_fingerprint) == len(settings_v2) == len(settings_v3) == 64
    assert len({settings_fingerprint, settings_v2, settings_v3}) == 3
    assert len(learning_v1) == len(learning_v2) == len(learning_v3) == len(learning_v4) == len(learning_v5) == len(learning_v6) == len(learning_v7) == 64
    assert len({learning_v1, learning_v2, learning_v3, learning_v4, learning_v5, learning_v6, learning_v7, learning_v8}) == 8



def _create_untracked_schema(migrations, version):
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in migrations:
        if migration.version > version:
            break
        for statement in migration.statements:
            connection.execute(statement)
    connection.commit()
    return connection


def test_known_legacy_v1_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 1) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 8
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in history] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert len(history[0][1]) == 64
        assert all(row[1] is None for row in history[1:])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_known_v4_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 4) as connection:
        expected_v4 = fingerprint_for_version(LEARNING_MIGRATIONS, 4)
        assert schema_fingerprint(connection) == expected_v4
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 8
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(4, expected_v4), (5, None), (6, None), (7, None), (8, None)]


def test_known_v5_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 5) as connection:
        expected_v5 = fingerprint_for_version(LEARNING_MIGRATIONS, 5)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 8
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(5, expected_v5), (6, None), (7, None), (8, None)]


def test_known_v6_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 6) as connection:
        expected_v6 = fingerprint_for_version(LEARNING_MIGRATIONS, 6)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 8
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(6, expected_v6), (7, None), (8, None)]


def test_known_v7_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 7) as connection:
        expected_v7 = fingerprint_for_version(LEARNING_MIGRATIONS, 7)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 8
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(7, expected_v7), (8, None)]


def test_unknown_schema_is_not_mutated_or_auto_adopted():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE unknown_table (id INTEGER PRIMARY KEY)")
        connection.commit()
        with pytest.raises(UntrackedSchemaError, match="does not match"):
            initialize_connection(connection, LEARNING_MIGRATIONS)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='unknown_table'"
        ).fetchone() == ("unknown_table",)


def test_schema_fingerprint_ignores_column_order_but_preserves_constraints():
    with sqlite3.connect(":memory:") as first, sqlite3.connect(":memory:") as second:
        first.execute(
            "CREATE TABLE sample (id INTEGER PRIMARY KEY, left_value TEXT, "
            "right_value INTEGER, CHECK(right_value >= 0))"
        )
        second.execute(
            "CREATE TABLE IF NOT EXISTS sample (right_value INTEGER, left_value TEXT, "
            "id INTEGER PRIMARY KEY, CHECK (right_value >= 0))"
        )
        assert schema_fingerprint(first) == schema_fingerprint(second)

    with sqlite3.connect(":memory:") as different:
        different.execute(
            "CREATE TABLE sample (id INTEGER PRIMARY KEY, left_value TEXT, "
            "right_value INTEGER, CHECK(right_value > 0))"
        )
        with sqlite3.connect(":memory:") as reference:
            reference.execute(
                "CREATE TABLE sample (id INTEGER PRIMARY KEY, left_value TEXT, "
                "right_value INTEGER, CHECK(right_value >= 0))"
            )
            assert schema_fingerprint(different) != schema_fingerprint(reference)



def test_current_application_schema_matches_registered_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()

    with db.settings_connection() as connection:
        assert schema_fingerprint(connection) == fingerprint_for_version(
            SETTINGS_MIGRATIONS, SETTINGS_MIGRATIONS[-1].version
        )
    with db.learning_connection() as connection:
        assert schema_fingerprint(connection) == fingerprint_for_version(LEARNING_MIGRATIONS, 8)



def test_file_initializer_backs_up_legacy_database_before_upgrade(tmp_path):
    path = tmp_path / "learning.db"
    with sqlite3.connect(path) as connection:
        for statement in LEARNING_MIGRATIONS[0].statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES (?, ?, ?)",
            ("preserve me", "queued", "test"),
        )

    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 8
    backups = list(tmp_path.glob("learning.db.pre-migration-*.bak"))
    assert len(backups) == 1

    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute(
            "SELECT topic, status FROM learning_jobs"
        ).fetchone() == ("preserve me", "queued")
        assert backup.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs"
        ).fetchone() == ("preserve me", "queued")
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 8


def test_file_initializer_does_not_make_backup_after_migration_is_current(tmp_path):
    path = tmp_path / "learning.db"
    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 8
    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 8
    assert list(tmp_path.glob("learning.db.pre-migration-*.bak")) == []


def test_file_initializer_backs_up_but_does_not_adopt_unknown_schema(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unknown_table (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO unknown_table(id) VALUES (42)")

    with pytest.raises(UntrackedSchemaError, match="does not match"):
        initialize_database_file(path, LEARNING_MIGRATIONS)

    backups = list(tmp_path.glob("unknown.db.pre-migration-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id FROM unknown_table").fetchone() == (42,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None



def test_migration_runner_rejects_out_of_band_schema_drift():
    with sqlite3.connect(":memory:") as connection:
        runner = MigrationRunner(LEARNING_MIGRATIONS)
        assert runner.apply(connection) == 8
        connection.execute("DROP INDEX idx_task_queue_ready")
        with pytest.raises(MigrationError, match="does not match the registered migration history"):
            runner.apply(connection)



def test_restore_backup_requires_opt_in_to_replace_and_restores_verified_file(tmp_path):
    path = tmp_path / "learning.db"
    with sqlite3.connect(path) as connection:
        for statement in LEARNING_MIGRATIONS[0].statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES (?, ?, ?)",
            ("restore me", "queued", "test"),
        )

    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 8
    backup = next(tmp_path.glob("learning.db.pre-migration-*.bak"))
    with pytest.raises(FileExistsError, match="overwrite=True"):
        restore_database_backup(backup, path)

    restored = restore_database_backup(backup, path, overwrite=True)
    assert restored == path
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs"
        ).fetchone() == ("restore me", "queued")
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
