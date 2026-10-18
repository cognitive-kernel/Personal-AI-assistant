"""Regression tests for safe degraded startup on an untracked SQLite schema."""
from fastapi.testclient import TestClient

from app import main
from app.schema_migrations import UntrackedSchemaError


def test_untracked_schema_keeps_dashboard_and_health_available(monkeypatch):
    def fail_initialization():
        raise UntrackedSchemaError(
            "Existing database schema does not match any registered migration version."
        )

    monkeypatch.setattr(main.db, "init_databases", fail_initialization)
    monkeypatch.setattr(main, "_startup_storage_error", None)

    with TestClient(main.app) as client:
        dashboard = client.get("/")
        health = client.get("/api/health")

        assert dashboard.status_code == 200
        assert "دستیار هوش مصنوعی شخصی" in dashboard.text
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"
        assert health.json()["reason"] == "untracked_storage_schema"
        assert health.json()["storage"]["mutations_enabled"] is False


def test_untracked_schema_blocks_database_routes_with_actionable_error(monkeypatch):
    monkeypatch.setattr(
        main.db,
        "init_databases",
        lambda: (_ for _ in ()).throw(UntrackedSchemaError("untracked schema")),
    )
    monkeypatch.setattr(main, "_startup_storage_error", None)

    with TestClient(main.app) as client:
        response = client.get("/api/learning/curricula")

        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "untracked_storage_schema"
        assert "inspect_sqlite_schema.py" in body["recovery_command"]
        assert "دیتابیس را حذف یا بازنشانی نکنید" in body["detail"]
