from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import cloud_resources, db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    with TestClient(app, client=("127.0.0.1", 12345)) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/tasks/0"),
        ("delete", "/api/tasks/0"),
        ("get", "/api/tasks/0/events"),
        ("post", "/api/tasks/0/pause"),
        ("post", "/api/tasks/0/resume"),
        ("post", "/api/tasks/0/cancel"),
        ("patch", "/api/tasks/0/priority"),
        ("patch", "/api/tasks/0/schedule"),
        ("post", "/api/learning/paths/0/curriculum-proposals/generate"),
        ("get", "/api/learning/paths/0/curriculum-proposals"),
        ("post", "/api/learning/curriculum-proposals/0/review"),
        ("get", "/api/cloud-resources/plans/0"),
        ("delete", "/api/cloud-resources/0"),
    ],
)
def test_integer_resource_path_ids_reject_zero_at_http_boundary(client, method, path):
    response = getattr(client, method)(path, json={} if method in {"post", "patch"} else None)
    assert response.status_code == 422, (method, path, response.text)


def test_integer_resource_path_id_constraints_are_published_in_openapi():
    schema = app.openapi()
    expected = {
        ("/api/tasks/{task_id}", "get", "task_id"),
        ("/api/tasks/{task_id}", "delete", "task_id"),
        ("/api/tasks/{task_id}/events", "get", "task_id"),
        ("/api/tasks/{task_id}/pause", "post", "task_id"),
        ("/api/tasks/{task_id}/resume", "post", "task_id"),
        ("/api/tasks/{task_id}/cancel", "post", "task_id"),
        ("/api/tasks/{task_id}/priority", "patch", "task_id"),
        ("/api/tasks/{task_id}/schedule", "patch", "task_id"),
        ("/api/learning/paths/{path_id}/curriculum-proposals/generate", "post", "path_id"),
        ("/api/learning/paths/{path_id}/curriculum-proposals", "get", "path_id"),
        ("/api/learning/curriculum-proposals/{proposal_id}/review", "post", "proposal_id"),
        ("/api/cloud-resources/plans/{plan_id}", "get", "plan_id"),
        ("/api/cloud-resources/{candidate_id}", "delete", "candidate_id"),
    }
    for path, method, name in expected:
        parameters = schema["paths"][path][method]["parameters"]
        parameter = next(item for item in parameters if item["name"] == name)
        assert parameter["schema"]["minimum"] == 1, (path, method, parameter)
