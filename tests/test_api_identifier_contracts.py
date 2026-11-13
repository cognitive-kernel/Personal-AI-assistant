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
    ("method", "path", "payload"),
    [
        ("get", "/api/tasks/0", None),
        ("delete", "/api/tasks/0", None),
        ("get", "/api/tasks/0/events", None),
        ("post", "/api/tasks/0/pause", None),
        ("post", "/api/tasks/0/resume", None),
        ("post", "/api/tasks/0/cancel", None),
        ("patch", "/api/tasks/0/priority", {"priority": 0}),
        ("patch", "/api/tasks/0/schedule", {"scheduled_at": "2026-11-14T12:00:00Z"}),
        ("post", "/api/learning/paths/0/curriculum-proposals/generate", None),
        ("get", "/api/learning/paths/0/curriculum-proposals", None),
        ("post", "/api/learning/curriculum-proposals/0/review", {"decision": "reject", "note": "This proposal is not sufficiently supported."}),
        ("get", "/api/cloud-resources/plans/0", None),
        ("delete", "/api/cloud-resources/0", None),
        ("get", "/api/ai-learning/runs/0", None),
        ("get", "/api/ai-learning/courses/0", None),
        ("get", "/api/ai-learning/versions/0", None),
        ("post", "/api/ai-learning/sources/0/check", None),
    ],
)
def test_integer_resource_path_ids_reject_zero_at_http_boundary(client, method, path, payload):
    if payload is None:
        response = getattr(client, method)(path)
    else:
        response = getattr(client, method)(path, json=payload)
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

def test_every_ai_learning_integer_path_identifier_is_positive_in_openapi():
    schema = app.openapi()
    inspected = 0
    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/ai-learning/"):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("in") != "path":
                    continue
                parameter_schema = parameter.get("schema", {})
                if parameter_schema.get("type") != "integer":
                    continue
                inspected += 1
                assert parameter_schema.get("minimum", 0) >= 1, (
                    method, path, parameter
                )
    assert inspected >= 25
