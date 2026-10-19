import json
import sqlite3

from app.schema_registry import LEARNING_MIGRATIONS
from scripts.inspect_sqlite_schema import inspect_database


def test_inspection_is_read_only_and_identifies_registered_legacy_schema(tmp_path):
    database = tmp_path / "learning.db"
    with sqlite3.connect(database) as connection:
        for statement in LEARNING_MIGRATIONS[0].statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO learning_jobs(topic, status, source_kind) VALUES (?, ?, ?)",
            ("private test topic", "queued", "test"),
        )
        connection.commit()

    before = database.read_bytes()
    report = inspect_database(database, "learning")

    assert report["read_only"] is True
    assert report["matching_registered_versions"] == [1]
    assert report["integrity_check"] == ["ok"]
    assert report["foreign_key_violation_count"] == 0
    assert database.read_bytes() == before
    rendered = json.dumps(report, ensure_ascii=False)
    assert "private test topic" not in rendered
    assert "schema_migrations" not in [item["name"] for item in report["objects"]]
