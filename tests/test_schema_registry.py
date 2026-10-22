import json
import sqlite3

import pytest

from app import db, task_manager
from app.schema_migrations import MigrationError, MigrationRunner, UntrackedSchemaError, schema_fingerprint
from app.schema_registry import (
    LEARNING_MIGRATIONS,
    LEGACY_LEARNING_SCHEMA_FINGERPRINT,
    SETTINGS_MIGRATIONS,
    fingerprint_for_version,
    initialize_connection,
    initialize_database_file,
    restore_database_backup,
)


def test_settings_schema_can_be_created_from_versioned_migrations():
    with sqlite3.connect(":memory:") as connection:
        runner = MigrationRunner(SETTINGS_MIGRATIONS)
        assert runner.apply(connection) == 4
        assert runner.apply(connection) == 4
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
        conversation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(chat_conversations)")
        }
        message_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(chat_messages)")
        }
        assert {"id", "title", "created_at", "updated_at"} <= conversation_columns
        assert {"id", "conversation_id", "role", "content", "task_id"} <= message_columns
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,)]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"



def test_settings_v3_upgrade_preserves_preferences_and_adds_chat_history():
    with sqlite3.connect(":memory:") as connection:
        assert MigrationRunner(SETTINGS_MIGRATIONS[:3]).apply(connection) == 3
        connection.execute(
            "INSERT INTO settings(key, value_json) VALUES (?, ?)",
            ("ollama_model", '"custom-local-model"'),
        )
        connection.commit()
        assert MigrationRunner(SETTINGS_MIGRATIONS).apply(connection) == 4
        assert connection.execute(
            "SELECT value_json FROM settings WHERE key='ollama_model'"
        ).fetchone() == ('"custom-local-model"',)
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"chat_conversations", "chat_messages"} <= tables
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

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 19
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 19

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
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,), (9,), (10,), (11,), (12,), (13,), (14,), (15,), (16,), (17,), (18,)]


def test_registry_exposes_reviewable_canonical_fingerprints():
    settings_fingerprint = fingerprint_for_version(SETTINGS_MIGRATIONS, 1)
    settings_v2 = fingerprint_for_version(SETTINGS_MIGRATIONS, 2)
    settings_v3 = fingerprint_for_version(SETTINGS_MIGRATIONS, 3)
    settings_v4 = fingerprint_for_version(SETTINGS_MIGRATIONS, 4)
    learning_v1 = fingerprint_for_version(LEARNING_MIGRATIONS, 1)
    learning_v2 = fingerprint_for_version(LEARNING_MIGRATIONS, 2)
    learning_v3 = fingerprint_for_version(LEARNING_MIGRATIONS, 3)
    learning_v4 = fingerprint_for_version(LEARNING_MIGRATIONS, 4)
    learning_v5 = fingerprint_for_version(LEARNING_MIGRATIONS, 5)
    learning_v6 = fingerprint_for_version(LEARNING_MIGRATIONS, 6)
    learning_v7 = fingerprint_for_version(LEARNING_MIGRATIONS, 7)
    learning_v8 = fingerprint_for_version(LEARNING_MIGRATIONS, 8)
    learning_v9 = fingerprint_for_version(LEARNING_MIGRATIONS, 9)
    learning_v10 = fingerprint_for_version(LEARNING_MIGRATIONS, 10)
    learning_v11 = fingerprint_for_version(LEARNING_MIGRATIONS, 11)
    learning_v12 = fingerprint_for_version(LEARNING_MIGRATIONS, 12)
    learning_v13 = fingerprint_for_version(LEARNING_MIGRATIONS, 13)
    learning_v14 = fingerprint_for_version(LEARNING_MIGRATIONS, 14)
    learning_v15 = fingerprint_for_version(LEARNING_MIGRATIONS, 15)
    learning_v16 = fingerprint_for_version(LEARNING_MIGRATIONS, 16)
    learning_v17 = fingerprint_for_version(LEARNING_MIGRATIONS, 17)
    learning_v18 = fingerprint_for_version(LEARNING_MIGRATIONS, 18)
    learning_v19 = fingerprint_for_version(LEARNING_MIGRATIONS, 19)

    assert len(settings_fingerprint) == len(settings_v2) == len(settings_v3) == len(settings_v4) == 64
    assert len({settings_fingerprint, settings_v2, settings_v3, settings_v4}) == 4
    assert len(learning_v1) == len(learning_v2) == len(learning_v3) == len(learning_v4) == len(learning_v5) == len(learning_v6) == len(learning_v7) == 64
    assert len({learning_v1, learning_v2, learning_v3, learning_v4, learning_v5, learning_v6, learning_v7, learning_v8, learning_v9, learning_v10, learning_v11, learning_v12, learning_v13, learning_v14, learning_v15, learning_v16, learning_v17, learning_v18, learning_v19}) == 19



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
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 19
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in history] == list(range(1, 20))
        assert len(history[0][1]) == 64
        assert all(row[1] is None for row in history[1:])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_known_v4_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 4) as connection:
        expected_v4 = fingerprint_for_version(LEARNING_MIGRATIONS, 4)
        assert schema_fingerprint(connection) == expected_v4
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 19
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(4, expected_v4), (5, None), (6, None), (7, None), (8, None), (9, None), (10, None), (11, None), (12, None), (13, None), (14, None), (15, None), (16, None), (17, None), (18, None), (19, None)]


def test_known_v5_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 5) as connection:
        expected_v5 = fingerprint_for_version(LEARNING_MIGRATIONS, 5)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 19
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(5, expected_v5), (6, None), (7, None), (8, None), (9, None), (10, None), (11, None), (12, None), (13, None), (14, None), (15, None), (16, None), (17, None), (18, None), (19, None)]


def test_known_v6_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 6) as connection:
        expected_v6 = fingerprint_for_version(LEARNING_MIGRATIONS, 6)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 19
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(6, expected_v6), (7, None), (8, None), (9, None), (10, None), (11, None), (12, None), (13, None), (14, None), (15, None), (16, None), (17, None), (18, None), (19, None)]


def test_known_v7_schema_is_baselined_then_upgraded():
    with _create_untracked_schema(LEARNING_MIGRATIONS, 7) as connection:
        expected_v7 = fingerprint_for_version(LEARNING_MIGRATIONS, 7)
        assert initialize_connection(connection, LEARNING_MIGRATIONS) == 19
        history = connection.execute(
            "SELECT version, baseline_fingerprint FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert history == [(7, expected_v7), (8, None), (9, None), (10, None), (11, None), (12, None), (13, None), (14, None), (15, None), (16, None), (17, None), (18, None), (19, None)]


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
        assert schema_fingerprint(connection) == fingerprint_for_version(LEARNING_MIGRATIONS, LEARNING_MIGRATIONS[-1].version)



def test_file_initializer_backs_up_legacy_database_before_upgrade(tmp_path):
    path = tmp_path / "learning.db"
    with sqlite3.connect(path) as connection:
        for statement in LEARNING_MIGRATIONS[0].statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES (?, ?, ?)",
            ("preserve me", "queued", "test"),
        )

    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 19
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
        ).fetchone()[0] == 19


def test_file_initializer_does_not_make_backup_after_migration_is_current(tmp_path):
    path = tmp_path / "learning.db"
    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 19
    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 19
    assert list(tmp_path.glob("learning.db.pre-migration-*.bak")) == []


def test_file_initializer_backs_up_but_does_not_adopt_unknown_schema(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unknown_table (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO unknown_table(id) VALUES (42)")

    with pytest.raises(UntrackedSchemaError, match="does not match") as error:
        initialize_database_file(path, LEARNING_MIGRATIONS)

    assert f"Database path: {path.resolve()}" in str(error.value)
    assert "The existing database was not migrated or modified." in str(error.value)
    assert "Verified pre-migration backup:" in str(error.value)
    assert "Observed schema fingerprint:" in str(error.value)
    assert "scripts/inspect_sqlite_schema.py" in str(error.value)
    assert "--kind learning" in str(error.value)
    assert "Do not reset" in str(error.value)
    backups = list(tmp_path.glob("unknown.db.pre-migration-*.bak"))
    assert len(backups) == 1
    assert str(backups[0].resolve()) in str(error.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id FROM unknown_table").fetchone() == (42,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None



def test_migration_runner_rejects_out_of_band_schema_drift():
    with sqlite3.connect(":memory:") as connection:
        runner = MigrationRunner(LEARNING_MIGRATIONS)
        assert runner.apply(connection) == 19
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
    # sqlite3.Connection's context manager commits but does not close the handle.
    # Windows cannot replace a database file while this connection remains open.
    connection.close()

    assert initialize_database_file(path, LEARNING_MIGRATIONS) == 19
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


def _create_observed_legacy_learning_database(path, *, invalid_experience_status=None):
    """Reconstruct the reviewed target-host schema shape for migration regression tests."""
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        MigrationRunner(LEARNING_MIGRATIONS[:5]).apply(connection)
        connection.execute("DROP TABLE learning_experience_versions")
        connection.execute("DROP TABLE learning_experiences")
        connection.execute("DROP TABLE task_queue")
        connection.execute("DROP TABLE schema_migrations")
        connection.execute(
            """CREATE TABLE learning_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                problem TEXT NOT NULL,
                hypothesis TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'identified',
                source_evidence_json TEXT NOT NULL DEFAULT '[]',
                test_plan_json TEXT NOT NULL DEFAULT '[]',
                test_results_json TEXT NOT NULL DEFAULT '[]',
                takeaway TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_learning_experiences_topic ON learning_experiences(topic, id DESC)"
        )
        connection.execute(
            "CREATE INDEX idx_learning_experiences_status ON learning_experiences(status, id DESC)"
        )
        connection.execute(
            """CREATE TABLE learning_experience_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id INTEGER NOT NULL REFERENCES learning_experiences(id) ON DELETE CASCADE,
                version INTEGER NOT NULL, topic TEXT NOT NULL, problem TEXT NOT NULL,
                hypothesis TEXT NOT NULL, status TEXT NOT NULL,
                source_evidence_json TEXT NOT NULL, test_plan_json TEXT NOT NULL,
                test_results_json TEXT NOT NULL, takeaway TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(experience_id, version)
            )"""
        )
        connection.execute(
            "CREATE INDEX idx_experience_versions_lookup ON learning_experience_versions(experience_id, version DESC)"
        )
        connection.execute(
            """CREATE TABLE chat_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                rating TEXT NOT NULL CHECK(rating IN ('helpful','unhelpful')),
                correction TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """CREATE TABLE knowledge_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                license_note TEXT NOT NULL DEFAULT 'Reuse terms not independently reviewed; retain source attribution.',
                status TEXT NOT NULL DEFAULT 'indexed',
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            """CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL, content TEXT NOT NULL,
                embedding_json TEXT, embedding_model TEXT,
                UNIQUE(source_id, chunk_index)
            )"""
        )
        connection.execute("CREATE VIRTUAL TABLE knowledge_chunks_fts USING fts5(content)")
        connection.execute("CREATE INDEX idx_chat_feedback_rating ON chat_feedback(rating, id DESC)")
        connection.execute(
            "CREATE INDEX idx_knowledge_chunks_source ON knowledge_chunks(source_id, chunk_index)"
        )
        if invalid_experience_status is not None:
            connection.execute(
                "INSERT INTO learning_experiences(topic, problem, status) VALUES (?, ?, ?)",
                ("legacy status fixture", "test migration rollback", invalid_experience_status),
            )
        connection.commit()
    connection.close()
    return path


def _seed_observed_legacy_learning_data(path):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        path_id = connection.execute(
            "INSERT INTO learning_paths(curriculum_slug, title) VALUES ('legacy-path', 'Legacy path')"
        ).lastrowid
        topic_id = connection.execute(
            "INSERT INTO learning_path_topics(path_id, topic, position, status) VALUES (?, 'legacy topic', 0, 'completed')",
            (path_id,),
        ).lastrowid
        job_id = connection.execute(
            """INSERT INTO learning_jobs(topic, status, source_kind, quality_json, path_topic_id, lesson_review_json)
               VALUES ('legacy topic', 'succeeded', 'user_text', '{}', ?, '{}')""",
            (topic_id,),
        ).lastrowid
        connection.execute(
            "INSERT INTO learning_evidence(job_id, evidence_type, source_ref, content_hash, note) VALUES (?, 'url', 'https://example.test/source', 'abc', 'retained')",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO learning_source_text(job_id, content) VALUES (?, 'legacy source body')",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id) VALUES ('legacy topic', 'legacy lesson', 'user_text', 1, 'unverified', ?)",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO assessment_attempts(job_id, answers_json, score, correct_count, question_count) VALUES (?, '[\"a\"]', 100, 1, 1)",
            (job_id,),
        )
        connection.execute(
            "INSERT INTO lesson_reviews(job_id, outcomes_json, value_rating, takeaway, issues) VALUES (?, '[\"useful\"]', 5, 'retain review', '')",
            (job_id,),
        )
        experience_id = connection.execute(
            "INSERT INTO learning_experiences(topic, problem, status, takeaway) VALUES ('legacy experience', 'legacy problem', 'verified', 'keep')"
        ).lastrowid
        connection.execute(
            """INSERT INTO learning_experience_versions(
                   experience_id, version, topic, problem, hypothesis, status,
                   source_evidence_json, test_plan_json, test_results_json, takeaway
               ) VALUES (?, 1, 'legacy experience', 'legacy problem', '', 'verified', '[]', '[]', '[]', 'keep')""",
            (experience_id,),
        )
        connection.execute(
            "INSERT INTO chat_feedback(query, answer, rating, correction) VALUES ('q', 'a', 'unhelpful', 'corrected')"
        )
        # Simulate deleted tail rows so migration must preserve the sequence high-water mark.
        connection.execute(
            "UPDATE sqlite_sequence SET seq = 80 WHERE name = 'chat_feedback'"
        )
        source_id = connection.execute(
            "INSERT INTO knowledge_sources(url, title, content_hash) VALUES ('https://example.test/knowledge', 'Legacy source', 'sha256-test')"
        ).lastrowid
        connection.execute(
            "INSERT INTO knowledge_chunks(source_id, chunk_index, content, embedding_json, embedding_model) VALUES (?, 0, 'legacychunk searchable text', NULL, NULL)",
            (source_id,),
        )
        connection.commit()
    connection.close()


def test_observed_legacy_learning_fixture_matches_reviewed_fingerprint(tmp_path):
    path = _create_observed_legacy_learning_database(tmp_path / "learning.db")
    with sqlite3.connect(path) as connection:
        assert schema_fingerprint(connection) == LEGACY_LEARNING_SCHEMA_FINGERPRINT
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_observed_legacy_learning_schema_migrates_with_backup_and_row_preservation(tmp_path):
    path = _create_observed_legacy_learning_database(tmp_path / "learning.db")
    _seed_observed_legacy_learning_data(path)

    version = initialize_database_file(path, LEARNING_MIGRATIONS)

    assert version == 19
    backups = list(tmp_path.glob("learning.db.pre-migration-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert schema_fingerprint(backup) == LEGACY_LEARNING_SCHEMA_FINGERPRINT
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT content FROM knowledge_items").fetchone() == ("legacy lesson",)
        assert connection.execute("SELECT correction FROM chat_feedback").fetchone() == ("corrected",)
        assert connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'chat_feedback'"
        ).fetchone() == (80,)
        assert connection.execute("SELECT content FROM knowledge_chunks").fetchone() == ("legacychunk searchable text",)
        assert connection.execute(
            "SELECT content FROM knowledge_chunks_fts WHERE knowledge_chunks_fts MATCH 'legacychunk'"
        ).fetchone() == ("legacychunk searchable text",)
        assert connection.execute(
            "SELECT status, takeaway FROM learning_experiences"
        ).fetchone() == ("verified", "keep")
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, 20)]


def test_legacy_learning_migration_failure_preserves_original_database_bytes(tmp_path):
    path = _create_observed_legacy_learning_database(
        tmp_path / "learning.db", invalid_experience_status="unknown-status"
    )
    before = path.read_bytes()

    with pytest.raises(UntrackedSchemaError, match="verified backup") as error:
        initialize_database_file(path, LEARNING_MIGRATIONS)

    assert str(path) in str(error.value)
    assert path.read_bytes() == before
    backups = list(tmp_path.glob("learning.db.pre-migration-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(path) as connection:
        assert schema_fingerprint(connection) == LEGACY_LEARNING_SCHEMA_FINGERPRINT
        assert connection.execute("SELECT status FROM learning_experiences").fetchone() == ("unknown-status",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


def test_application_database_initializer_migrates_observed_legacy_learning_store(tmp_path, monkeypatch):
    """Exercise the exact db.init_databases() startup path with a disposable legacy DB."""
    settings_path = tmp_path / "settings.db"
    learning_path = _create_observed_legacy_learning_database(tmp_path / "learning.db")
    _seed_observed_legacy_learning_data(learning_path)
    monkeypatch.setattr(db.defaults, "settings_db_path", str(settings_path))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(learning_path))

    db.init_databases()

    with sqlite3.connect(learning_path) as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 19
        assert connection.execute(
            "SELECT content FROM knowledge_items"
        ).fetchone() == ("legacy lesson",)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert list(tmp_path.glob("learning.db.pre-migration-*.bak"))
    assert db.get_settings()
