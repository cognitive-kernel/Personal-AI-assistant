import pytest
from fastapi.testclient import TestClient

from app import cloud_resources, db, sandbox_tasks
from app.main import app
from app.sandbox import SandboxResult


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )

    docker = tmp_path / "docker"
    docker.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(docker))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    monkeypatch.setattr(
        sandbox_tasks.sandbox,
        "run_python",
        lambda source, **kwargs: SandboxResult("succeeded", 0, "ok", "", 1),
    )

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        yield client


def test_sandbox_consent_is_off_by_default_and_audits_grant_and_revoke(local_client):
    status = local_client.get("/api/sandbox/consent")
    assert status.status_code == 200
    assert status.json()["consent"]["enabled"] is False
    assert status.json()["runtime_configured"] is True

    denied = local_client.post("/api/sandbox/consent", json={"confirm": False})
    assert denied.status_code == 422

    granted = local_client.post("/api/sandbox/consent", json={"confirm": True})
    assert granted.status_code == 200
    assert granted.json()["consent"]["enabled"] is True
    assert granted.json()["consent"]["consented_at"]

    revoked = local_client.delete("/api/sandbox/consent")
    assert revoked.status_code == 200
    consent = revoked.json()["consent"]
    assert consent["enabled"] is False
    assert consent["revoked_at"]
    assert [event["event_type"] for event in consent["events"][:2]] == [
        "consent_revoked",
        "consent_granted",
    ]


def test_sandbox_run_requires_consent_then_queues_a_bounded_task(local_client):
    payload = {"source_code": "print('ok')", "timeout_seconds": 5, "max_output_bytes": 1000}
    denied = local_client.post("/api/sandbox/run", json=payload)
    assert denied.status_code == 409

    assert local_client.post("/api/sandbox/consent", json={"confirm": True}).status_code == 200
    queued = local_client.post("/api/sandbox/run", json=payload)
    assert queued.status_code == 202
    task = queued.json()["task"]
    assert task["task_type"] == "sandbox.run_python"
    assert task["payload"] == payload
    assert task["max_attempts"] == 1


def test_generic_task_endpoint_cannot_bypass_sandbox_consent_route(local_client):
    response = local_client.post(
        "/api/tasks",
        json={"task_type": "sandbox.run_python", "payload": {"source_code": "print('bypass')"}},
    )
    assert response.status_code == 422
    assert "consent" in response.json()["detail"]


def test_sandbox_routes_are_loopback_only(tmp_path, monkeypatch):
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("SANDBOX_DOCKER_EXECUTABLE", raising=False)
    monkeypatch.delenv("SANDBOX_PYTHON_IMAGE", raising=False)
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud.db"))
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/sandbox/consent")
        assert response.status_code == 403
