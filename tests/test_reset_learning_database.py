import sqlite3
from pathlib import Path

import pytest

from app.schema_registry import LEARNING_MIGRATIONS
from scripts.reset_learning_database import reset_learning_database


def test_reset_learning_database_recreates_latest_schema_and_discards_old_rows(tmp_path: Path):
    database = tmp_path / "learning.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE disposable_test_data (value TEXT)")
        connection.execute(
            "INSERT INTO disposable_test_data(value) VALUES ('only a test row')"
        )
    Path(str(database) + "-wal").write_bytes(b"")
    Path(str(database) + "-shm").write_bytes(b"")

    result = reset_learning_database(database)

    assert result["reset_performed"] is True
    assert result["schema_version"] == LEARNING_MIGRATIONS[-1].version
    assert result["integrity_check"] == "ok"
    assert result["settings_database_touched"] is False
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "learning_jobs" in tables
        assert "disposable_test_data" not in tables
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, LEARNING_MIGRATIONS[-1].version + 1)]


def test_reset_learning_database_refuses_to_delete_corrupt_database(tmp_path: Path):
    database = tmp_path / "learning.db"
    database.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        reset_learning_database(database)

    assert database.read_bytes() == b"not a sqlite database"
