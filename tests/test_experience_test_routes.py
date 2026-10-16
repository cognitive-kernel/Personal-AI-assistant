from fastapi.testclient import TestClient

from app import (
    cloud_resources,
    db,
    experience,
    experience_routes,
    sandbox_consent,
    task_manager,
)
from app.main import app


def _setup(tmp_path, monkeypatch):
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
    db.init_databases()
    item = experience.create_experience("Python", "Empty input regression.")
    experience.update_experience(
        item["id"],
        source_evidence=[{"url": "https://docs.python.org/3/library/", "claim": "Official docs"}],
        test_plan=[{"test": "empty input regression"}],
        takeaway="Test the empty-input branch.",
    )
    return item["id"]


def test_experience_test_route_requires_consent_and_queues_only_planned_tests(
    tmp_path, monkeypatch
):
    experience_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        experience_routes.experience_tasks,
        "handler_from_environment",
        lambda: (lambda *_args, **_kwargs: {}),
    )
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: False)
    captured = {}
    monkeypatch.setattr(
        task_manager,
        "create_task",
        lambda task_type, payload, **kwargs: captured.update(
            task_type=task_type, payload=payload, options=kwargs
        ) or {"id": 987, "status": "queued", "task_type": task_type},
    )

    body = {
        "confirm": True,
        "test_name": "empty input regression",
        "source_code": "assert [] == list()",
        "timeout_seconds": 5,
        "max_output_bytes": 1000,
    }
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        blocked = client.post(f"/api/experiences/{experience_id}/run-test", json=body)
        assert blocked.status_code == 409

        monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
        queued = client.post(f"/api/experiences/{experience_id}/run-test", json=body)
        assert queued.status_code == 202
        assert queued.json()["task"]["id"] == 987

        no_assert = client.post(
            f"/api/experiences/{experience_id}/run-test",
            json={**body, "source_code": "print('ok')"},
        )
        assert no_assert.status_code == 422

    assert captured["task_type"] == "experience.run_test"
    assert captured["payload"]["experience_id"] == experience_id
    assert captured["options"]["max_attempts"] == 1


def test_experience_test_route_rejects_unplanned_test_and_missing_confirmation(
    tmp_path, monkeypatch
):
    experience_id = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        experience_routes.experience_tasks,
        "handler_from_environment",
        lambda: (lambda *_args, **_kwargs: {}),
    )
    monkeypatch.setattr(sandbox_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(task_manager, "create_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not queue")))
    body = {
        "confirm": True,
        "test_name": "not in the plan",
        "source_code": "assert True",
    }
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        unplanned = client.post(f"/api/experiences/{experience_id}/run-test", json=body)
        assert unplanned.status_code == 422
        no_confirm = client.post(
            f"/api/experiences/{experience_id}/run-test",
            json={**body, "confirm": False, "test_name": "empty input regression"},
        )
        assert no_confirm.status_code == 422
