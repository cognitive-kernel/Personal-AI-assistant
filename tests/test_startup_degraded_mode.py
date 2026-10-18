from fastapi.testclient import TestClient

from app import main
from app.schema_migrations import UntrackedSchemaError


def test_untracked_learning_schema_keeps_dashboard_and_diagnostics_available(monkeypatch):
    def fail_initialization():
        raise UntrackedSchemaError(
            "Existing database schema does not match any registered migration version."
        )

    monkeypatch.setattr(main.db, "init_databases", fail_initialization)
    monkeypatch.setattr(main.ollama, "health", lambda: {"status": "unavailable"})
    monkeypatch.setattr(main, "detect_hardware", lambda: {})
    monkeypatch.setattr(main, "runtime_limits", lambda settings, hardware: {})

    with TestClient(main.app) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "دستیار هوش مصنوعی شخصی" in dashboard.text

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"
        assert "registered migration version" in health.json()["startup_blocker"]

        blocked = client.get("/api/models")
        assert blocked.status_code == 503
        assert blocked.json()["code"] == "storage_schema_untracked"
        assert "inspect_sqlite_schema.py" in blocked.json()["recovery"]


def test_normal_startup_does_not_report_storage_degraded_mode(monkeypatch):
    monkeypatch.setattr(main.db, "init_databases", lambda: None)
    monkeypatch.setattr(main.ollama, "health", lambda: {"status": "unavailable"})
    monkeypatch.setattr(main, "detect_hardware", lambda: {})
    monkeypatch.setattr(main, "runtime_limits", lambda settings, hardware: {})

    # The full startup also starts background workers and requires initialized
    # stores, so test the readiness state through the app's health handler
    # with startup blocker cleared rather than starting runtime workers.
    main._startup_blocker = None
    response = main.api_health()
    assert response["status"] == "ok"
    assert response["startup_blocker"] is None
