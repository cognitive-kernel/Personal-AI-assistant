import sqlite3

from app.schema_migrations import MigrationRunner, UntrackedSchemaError, schema_fingerprint
from app.schema_registry import (
    LEARNING_LEGACY_BASELINES,
    LEARNING_MIGRATIONS,
    SETTINGS_LEGACY_BASELINES,
    SETTINGS_MIGRATIONS,
    baseline_known_legacy_schema,
    fingerprint_for_version,
)


def _tables(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _legacy_settings():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    return connection


def _legacy_learning(*, quality=True, path_topic=True, queue_mode="none"):
    """Reproduce reviewed pre-runner DDL shapes, including ALTER TABLE ordering."""
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    quality_column = ", quality_json TEXT" if quality else ""
    connection.execute(f"""CREATE TABLE IF NOT EXISTS learning_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, status TEXT NOT NULL,
            source_kind TEXT NOT NULL, lesson TEXT, assessment_json TEXT{quality_column}, error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS learning_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
            evidence_type TEXT NOT NULL, source_ref TEXT NOT NULL, content_hash TEXT,
            note TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS learning_source_text (
            job_id INTEGER PRIMARY KEY REFERENCES learning_jobs(id) ON DELETE CASCADE,
            content TEXT NOT NULL)""")
    if path_topic:
        connection.execute(
            "ALTER TABLE learning_jobs ADD COLUMN path_topic_id INTEGER "
            "REFERENCES learning_path_topics(id) ON DELETE SET NULL"
        )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_learning_jobs_status ON learning_jobs(status)")
    if path_topic:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_jobs_path_topic ON learning_jobs(path_topic_id, status)"
        )
    connection.execute("""CREATE TABLE IF NOT EXISTS knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, content TEXT NOT NULL,
            source_kind TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'unverified',
            job_id INTEGER NOT NULL UNIQUE REFERENCES learning_jobs(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(topic, version))""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge_items(topic, version DESC)")
    connection.execute("""CREATE TABLE IF NOT EXISTS assessment_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
            answers_json TEXT NOT NULL, score REAL NOT NULL,
            correct_count INTEGER NOT NULL, question_count INTEGER NOT NULL,
            scoring_method TEXT NOT NULL DEFAULT 'normalized_exact_match',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_assessment_attempts_job ON assessment_attempts(job_id, id DESC)")
    connection.execute("""CREATE TABLE IF NOT EXISTS lesson_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES learning_jobs(id) ON DELETE CASCADE,
            outcomes_json TEXT NOT NULL,
            value_rating INTEGER NOT NULL CHECK(value_rating BETWEEN 1 AND 5),
            takeaway TEXT NOT NULL DEFAULT '',
            issues TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_lesson_reviews_job ON lesson_reviews(job_id, id DESC)")
    connection.execute("""CREATE TABLE IF NOT EXISTS learning_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            curriculum_slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS learning_path_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path_id INTEGER NOT NULL REFERENCES learning_paths(id) ON DELETE CASCADE,
            topic TEXT NOT NULL, position INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            latest_score REAL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(path_id, topic), UNIQUE(path_id, position))""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_path_topics_status ON learning_path_topics(path_id, status, position)"
    )
    if queue_mode != "none":
        has_dedupe = queue_mode == "create_dedupe"
        dedupe_sql = ",\n                dedupe_key TEXT" if has_dedupe else ""
        connection.execute(f"""
            CREATE TABLE IF NOT EXISTS task_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL CHECK(length(trim(task_type)) > 0),
                payload_json TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','running','pause_requested','paused',
                                     'cancel_requested','cancelled','succeeded','failed')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                max_attempts INTEGER NOT NULL DEFAULT 1 CHECK(max_attempts >= 1),
                checkpoint_json TEXT,
                result_json TEXT,
                error TEXT,
                scheduled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                lease_owner TEXT,
                lease_expires_at TEXT{dedupe_sql},
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_queue_ready ON task_queue(status, scheduled_at, priority DESC, id)"
        )
        if queue_mode == "alter_dedupe":
            connection.execute("ALTER TABLE task_queue ADD COLUMN dedupe_key TEXT")
        if queue_mode in {"create_dedupe", "alter_dedupe"}:
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_queue_dedupe_key "
                "ON task_queue(dedupe_key) WHERE dedupe_key IS NOT NULL"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_queue_lease ON task_queue(status, lease_expires_at)"
        )
    return connection


def test_fresh_settings_schema_is_versioned_and_idempotent():
    with sqlite3.connect(":memory:") as connection:
        runner = MigrationRunner(SETTINGS_MIGRATIONS)
        assert runner.apply(connection) == 1
        assert runner.apply(connection) == 1
        assert _tables(connection) == {"settings", "schema_migrations"}
        assert connection.execute(
            "SELECT version, migration_id FROM schema_migrations"
        ).fetchall() == [(1, "settings-initial")]


def test_fresh_learning_schema_migrates_all_five_versions():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        runner = MigrationRunner(LEARNING_MIGRATIONS)
        assert runner.apply(connection) == 5
        assert runner.apply(connection) == 5
        assert {
            "learning_jobs", "learning_evidence", "learning_source_text",
            "knowledge_items", "assessment_attempts", "lesson_reviews",
            "learning_paths", "learning_path_topics", "task_queue", "schema_migrations",
        } <= _tables(connection)
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_jobs)")}
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(task_queue)")}
        assert {"quality_json", "path_topic_id"} <= job_columns
        assert {"dedupe_key", "checkpoint_json", "lease_owner", "lease_expires_at"} <= task_columns
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,)]


def test_upgrade_from_version_one_preserves_learning_job():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert MigrationRunner(LEARNING_MIGRATIONS[:1]).apply(connection) == 1
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('retained', 'queued', 'test')"
        ).lastrowid
        connection.commit()

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 5
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("retained", "queued")


def test_upgrade_from_version_four_preserves_task_and_adds_dedupe_key():
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert MigrationRunner(LEARNING_MIGRATIONS[:4]).apply(connection) == 4
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('retained', 'queued', 'test')"
        ).lastrowid
        task_id = connection.execute(
            "INSERT INTO task_queue(task_type, payload_json) VALUES ('learning.run_job', ?)",
            ('{"job_id": 1}',),
        ).lastrowid
        connection.commit()

        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 5
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("retained", "queued")
        assert connection.execute(
            "SELECT task_type, payload_json FROM task_queue WHERE id=?", (task_id,)
        ).fetchone() == ("learning.run_job", '{"job_id": 1}')
        assert "dedupe_key" in {
            row[1] for row in connection.execute("PRAGMA table_info(task_queue)")
        }


def test_reviewed_legacy_fingerprints_match_known_schema_variants():
    settings = _legacy_settings()
    try:
        settings_fingerprint = schema_fingerprint(settings)
        assert SETTINGS_LEGACY_BASELINES[settings_fingerprint].version == 1
    finally:
        settings.close()

    variants = (
        (_legacy_learning(quality=False, path_topic=False), 1),
        (_legacy_learning(quality=True, path_topic=False), 2),
        (_legacy_learning(quality=True, path_topic=True), 3),
        (_legacy_learning(quality=True, path_topic=True, queue_mode="no_dedupe"), 4),
        (_legacy_learning(quality=True, path_topic=True, queue_mode="create_dedupe"), 5),
        (_legacy_learning(quality=True, path_topic=True, queue_mode="alter_dedupe"), 5),
    )
    for connection, expected_version in variants:
        try:
            fingerprint = schema_fingerprint(connection)
            assert LEARNING_LEGACY_BASELINES[fingerprint].version == expected_version
        finally:
            connection.close()


def test_known_legacy_baseline_preserves_data_and_upgrades_forward():
    connection = _legacy_learning(quality=True, path_topic=True, queue_mode="none")
    try:
        job_id = connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES ('legacy lesson', 'queued', 'test')"
        ).lastrowid
        connection.commit()
        adopted = baseline_known_legacy_schema(
            connection, LEARNING_MIGRATIONS, LEARNING_LEGACY_BASELINES
        )
        assert adopted == 3
        assert MigrationRunner(LEARNING_MIGRATIONS).apply(connection) == 5
        assert connection.execute(
            "SELECT topic, status FROM learning_jobs WHERE id=?", (job_id,)
        ).fetchone() == ("legacy lesson", "queued")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_unknown_legacy_schema_is_rejected_without_creating_history():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE unfamiliar (id INTEGER PRIMARY KEY)")
        import pytest

        with pytest.raises(UntrackedSchemaError, match="not a reviewed legacy baseline"):
            baseline_known_legacy_schema(
                connection, LEARNING_MIGRATIONS, LEARNING_LEGACY_BASELINES
            )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is None
    finally:
        connection.close()


def test_registry_exposes_reviewable_canonical_fingerprints():
    settings_fingerprint = fingerprint_for_version(SETTINGS_MIGRATIONS, 1)
    learning_fingerprints = [
        fingerprint_for_version(LEARNING_MIGRATIONS, version) for version in range(1, 6)
    ]
    assert len(settings_fingerprint) == 64
    assert all(len(value) == 64 for value in learning_fingerprints)
    assert len(set(learning_fingerprints)) == 5
