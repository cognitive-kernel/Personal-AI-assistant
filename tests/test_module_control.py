from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, module_control, task_manager
from app.main import app
from app.task_routes import FEATURE_MANAGED_TASK_TYPES


@pytest.fixture
def module_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()


def test_pause_and_resume_gate_only_the_selected_module(module_store):
    cloud = task_manager.create_task("cloud.discover_resources", {"manual": True})
    research = task_manager.create_task("research.answer", {"question": "test"})

    paused = module_control.control_module("cloud-discovery", "pause")
    assert paused["state"] == "paused"
    assert task_manager.get_task(cloud["id"])["status"] == "paused"

    assert task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    ) is None
    research_claim = task_manager.claim_next_task(
        "research-worker", task_types=("research.answer",)
    )
    assert research_claim["id"] == research["id"]

    resumed = module_control.control_module("cloud-discovery", "resume")
    assert resumed["state"] == "running"
    assert task_manager.get_task(cloud["id"])["status"] == "queued"
    cloud_claim = task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    )
    assert cloud_claim["id"] == cloud["id"]


def test_pause_requests_checkpoint_and_resume_preserves_it(module_store):
    task = task_manager.create_task("cloud.discover_resources", {"manual": True})
    claimed = task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    )
    assert claimed["id"] == task["id"]

    paused = module_control.control_module("cloud-discovery", "pause")
    assert paused["task_counts"]["active"] == 1
    requested = task_manager.get_task(task["id"])
    assert requested["status"] == "pause_requested"

    settled = task_manager.save_checkpoint(
        task["id"], claimed["lease_owner"], {"cursor": "source-17"},
        lease_epoch=claimed["lease_epoch"],
    )
    assert settled["status"] == "paused"
    assert settled["checkpoint"] == {"cursor": "source-17"}

    resumed = module_control.control_module("cloud-discovery", "resume")
    assert resumed["state"] == "running"
    queued = task_manager.get_task(task["id"])
    assert queued["status"] == "queued"
    assert queued["checkpoint"] == {"cursor": "source-17"}


def test_finish_drains_active_work_without_claiming_or_deleting_queued_work(module_store):
    active = task_manager.create_task("cloud.discover_resources", {"item": "active"})
    waiting = task_manager.create_task("cloud.discover_resources", {"item": "waiting"})
    claimed = task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    )
    assert claimed["id"] == active["id"]

    finishing = module_control.control_module("cloud-discovery", "finish")
    assert finishing["state"] == "draining"
    assert task_manager.get_task(active["id"])["status"] == "running"
    assert task_manager.get_task(waiting["id"])["status"] == "queued"
    assert task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    ) is None

    completed = task_manager.complete_task(
        active["id"], claimed["lease_owner"], {"ok": True},
        lease_epoch=claimed["lease_epoch"],
    )
    assert completed["status"] == "succeeded"
    status = next(
        item for item in module_control.list_module_controls()
        if item["id"] == "cloud-discovery"
    )
    assert status["state"] == "draining"
    assert status["display_state"] == "finished_pending"
    assert task_manager.get_task(waiting["id"])["status"] == "queued"


def test_force_stop_cancels_waiting_tasks_and_cooperatively_stops_active_task(module_store):
    active = task_manager.create_task("cloud.discover_resources", {"item": "active"})
    waiting = task_manager.create_task("cloud.discover_resources", {"item": "waiting"})
    claimed = task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    )
    assert claimed["id"] == active["id"]

    stopped = module_control.control_module("cloud-discovery", "force_stop")
    assert stopped["state"] == "stopped"
    assert stopped["force_stop_semantics"].startswith("Python worker threads are not killed")
    assert task_manager.get_task(active["id"])["status"] == "cancel_requested"
    assert task_manager.get_task(waiting["id"])["status"] == "paused"

    settled = task_manager.save_checkpoint(
        active["id"], claimed["lease_owner"], {"cursor": "safe-boundary"},
        lease_epoch=claimed["lease_epoch"],
    )
    assert settled["status"] == "cancelled"
    assert settled["checkpoint"] == {"cursor": "safe-boundary"}

    resumed = module_control.control_module("cloud-discovery", "resume")
    assert resumed["state"] == "running"
    assert task_manager.get_task(waiting["id"])["status"] == "queued"


def test_start_does_not_implicitly_resume_previously_paused_tasks(module_store):
    task = task_manager.create_task("cloud.discover_resources", {"manual": True})
    module_control.control_module("cloud-discovery", "pause")
    started = module_control.control_module("cloud-discovery", "start")

    assert started["state"] == "running"
    assert task_manager.get_task(task["id"])["status"] == "paused"
    assert task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    ) is None


def test_module_control_rejects_unknown_module_and_action(module_store):
    with pytest.raises(module_control.ModuleNotFoundError):
        module_control.control_module("not-a-module", "pause")
    with pytest.raises(module_control.ModuleControlError):
        module_control.control_module("cloud-discovery", "kill")


def test_module_control_api_is_loopback_only_and_returns_no_task_payloads(module_store):
    client = TestClient(app, client=("127.0.0.1", 12345))
    listed = client.get("/api/tasks/modules")
    assert listed.status_code == 200
    modules = listed.json()["modules"]
    cloud = next(item for item in modules if item["id"] == "cloud-discovery")
    assert cloud["state"] == "running"
    assert "payload" not in str(modules)

    paused = client.post("/api/tasks/modules/cloud-discovery/pause")
    assert paused.status_code == 200
    assert paused.json()["module"]["state"] == "paused"

    unknown = client.post("/api/tasks/modules/not-a-module/pause")
    assert unknown.status_code == 404
    invalid = client.post("/api/tasks/modules/cloud-discovery/kill")
    assert invalid.status_code == 422

    remote_client = TestClient(app, client=("192.0.2.20", 12345))
    forbidden = remote_client.get("/api/tasks/modules")
    assert forbidden.status_code == 403


def test_module_registry_covers_every_feature_managed_task_type():
    assert set(module_control.TASK_TYPE_TO_MODULE) == set(FEATURE_MANAGED_TASK_TYPES)

def test_finish_recovers_expired_active_lease_but_never_claims_new_queued_work(module_store):
    active = task_manager.create_task("cloud.discover_resources", {"item": "active"})
    waiting = task_manager.create_task("cloud.discover_resources", {"item": "waiting"})
    claimed = task_manager.claim_next_task(
        "cloud-worker", task_types=("cloud.discover_resources",)
    )
    task_manager.save_checkpoint(
        active["id"], claimed["lease_owner"], {"cursor": "resume-active"},
        lease_epoch=claimed["lease_epoch"],
    )
    module_control.control_module("cloud-discovery", "finish")

    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE task_queue SET lease_expires_at='2000-01-01T00:00:00.000000' WHERE id=?",
            (active["id"],),
        )

    recovered = task_manager.claim_next_task(
        "recovery-worker", task_types=("cloud.discover_resources",)
    )
    assert recovered["id"] == active["id"]
    assert recovered["lease_epoch"] > claimed["lease_epoch"]
    assert recovered["checkpoint"] == {"cursor": "resume-active"}
    assert task_manager.get_task(waiting["id"])["status"] == "queued"

    completed = task_manager.complete_task(
        active["id"], recovered["lease_owner"], {"recovered": True},
        lease_epoch=recovered["lease_epoch"],
    )
    assert completed["status"] == "succeeded"
    assert task_manager.get_task(waiting["id"])["status"] == "queued"
