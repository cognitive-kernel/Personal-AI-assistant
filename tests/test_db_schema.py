import sqlite3

from app import db, task_manager


def _init_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_current_sqlite_schema_contracts(tmp_path, monkeypatch):
    _init_stores(tmp_path, monkeypatch)

    with db.settings_connection() as conn:
        settings_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        settings_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(settings)")
        }

    with db.learning_connection() as conn:
        learning_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = {
            table: {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in learning_tables
        }
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(task_queue)")
        }
        foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert settings_tables == {"settings", "schema_migrations"}
    assert {"key", "value_json", "updated_at"} <= settings_columns
    with db.settings_connection() as conn:
        migration_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(schema_migrations)")
        }
        assert {"version", "migration_id", "checksum", "applied_at", "applied_mode"} == migration_columns
    assert {
        "learning_jobs",
        "learning_evidence",
        "learning_source_text",
        "knowledge_items",
        "assessment_attempts",
        "lesson_reviews",
        "learning_paths",
        "learning_path_topics",
        "task_queue",
    } <= learning_tables
    assert {"quality_json", "path_topic_id"} <= columns["learning_jobs"]
    assert {"dedupe_key", "lease_owner", "lease_expires_at", "checkpoint_json"} <= columns["task_queue"]
    assert {
        "idx_task_queue_ready",
        "idx_task_queue_lease",
        "idx_task_queue_dedupe_key",
    } <= indexes
    assert foreign_key_violations == []


def test_learning_children_cascade_when_job_is_deleted(tmp_path, monkeypatch):
    _init_stores(tmp_path, monkeypatch)

    with db.learning_connection() as conn:
        job_id = conn.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES (?, 'queued', ?)",
            ("Cascade contract", "user_provided_text"),
        ).lastrowid
        conn.execute(
            """INSERT INTO learning_evidence(job_id, evidence_type, source_ref, note)
               VALUES (?, 'user_text', 'test-source', 'fixture')""",
            (job_id,),
        )
        conn.execute(
            "INSERT INTO learning_source_text(job_id, content) VALUES (?, ?)",
            (job_id, "private source text"),
        )
        conn.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, job_id)
               VALUES (?, ?, ?, 1, ?)""",
            ("Cascade contract", "unverified fixture", "user_provided_text", job_id),
        )
        conn.execute(
            """INSERT INTO assessment_attempts(
                   job_id, answers_json, score, correct_count, question_count
               ) VALUES (?, '[]', 0, 0, 0)""",
            (job_id,),
        )
        conn.execute(
            """INSERT INTO lesson_reviews(job_id, outcomes_json, value_rating)
               VALUES (?, '[]', 3)""",
            (job_id,),
        )
        conn.execute("DELETE FROM learning_jobs WHERE id=?", (job_id,))
        for table in (
            "learning_evidence",
            "learning_source_text",
            "knowledge_items",
            "assessment_attempts",
            "lesson_reviews",
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE job_id=?", (job_id,)
            ).fetchone()[0] == 0


def test_database_initialization_is_idempotent_and_preserves_user_settings(
    tmp_path, monkeypatch
):
    _init_stores(tmp_path, monkeypatch)
    db.update_settings({"ollama_model": "user-selected-model"})

    db.init_databases()
    task_manager.initialize_task_store()

    assert db.get_settings()["ollama_model"] == "user-selected-model"
    with db.learning_connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
