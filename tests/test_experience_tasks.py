from types import SimpleNamespace

import pytest

from app import db, experience, experience_tasks, sandbox_tasks, task_manager
from app.sandbox import SandboxResult
from app.task_worker import TaskContext, TaskWorker


class FakeContext:
    def __init__(self):
        self.task_id = 501
        self.checkpoints = []
        self.commits = []
        self.status = "running"

    def checkpoint(self, value):
        self.checkpoints.append(value)
        return {"status": "running", "checkpoint": value}

    def complete_with_commit(self, checkpoint, operation):
        self.checkpoints.append(checkpoint)
        result = operation(None)
        self.commits.append(result)
        return result

    def control_status(self):
        return self.status


def _configure(monkeypatch, tmp_path):
    executable = tmp_path / "docker"
    executable.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(executable))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: True)


def _experience(status="identified"):
    return {
        "id": 17,
        "topic": "Python",
        "problem": "An empty input should produce an empty result.",
        "hypothesis": "The empty-input branch is missing.",
        "status": status,
        "version": 1,
        "source_evidence": [{"url": "https://docs.python.org/3/library/", "claim": "Documented behavior", "excerpt": "Documented behavior"}],
        "test_plan": [{"test": "empty input regression"}],
        "test_results": [],
        "takeaway": "",
    }


def test_runner_requires_configured_sandbox_and_assertion(monkeypatch):
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("SANDBOX_DOCKER_EXECUTABLE", raising=False)
    monkeypatch.delenv("SANDBOX_PYTHON_IMAGE", raising=False)
    assert experience_tasks.handler_from_environment() is None
    with pytest.raises(ValueError, match="assert statement"):
        experience_tasks.validate_test_harness("print('looks good')")


def test_controlled_runner_records_real_sandbox_success_and_verifies_only_with_evidence(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: _experience())
    monkeypatch.setattr(experience_tasks.web_sources, "fetch_public_document", lambda *args, **kwargs: SimpleNamespace(text="Documented behavior"))
    captured = {}

    def fake_update(connection, experience_id, **changes):
        captured.update(connection=connection, experience_id=experience_id, **changes)
        return {"id": experience_id, "version": 2, "status": changes["status"]}

    monkeypatch.setattr(
        experience_tasks.experience, "update_experience_in_transaction", fake_update
    )
    monkeypatch.setattr(
        sandbox_tasks.sandbox,
        "run_python",
        lambda _source, **_kwargs: SandboxResult("succeeded", 0, "assertion passed\n", "", 12),
    )

    handler = experience_tasks.handler_from_environment()
    assert handler is not None
    result = handler(
        {
            "experience_id": 17,
            "test_name": "empty input regression",
            "source_code": "assert [] == list()",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        },
        None,
        FakeContext(),
    )

    assert result["passed"] is True
    assert result["experience_status"] == "verified"
    assert captured["status"] == "verified"
    assert captured["_controlled_run"] is True
    assert captured["expected_version"] == 1
    assert captured["test_results"][-1]["passed"] is True
    assert captured["test_results"][-1]["runner"] == "docker-sandbox"
    assert captured["test_results"][-1]["source_sha256"]
    assert "source_code" not in captured["test_results"][-1]


def test_controlled_runner_records_failure_without_verification(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: _experience())
    monkeypatch.setattr(experience_tasks.web_sources, "fetch_public_document", lambda *args, **kwargs: SimpleNamespace(text="Documented behavior"))
    captured = {}

    def fake_update(connection, experience_id, **changes):
        captured.update(connection=connection, experience_id=experience_id, **changes)
        return {"id": experience_id, "version": 2, "status": changes["status"]}

    monkeypatch.setattr(
        experience_tasks.experience, "update_experience_in_transaction", fake_update
    )
    monkeypatch.setattr(
        sandbox_tasks.sandbox,
        "run_python",
        lambda _source, **_kwargs: SandboxResult("failed", 1, "", "AssertionError", 9),
    )

    result = experience_tasks.handler_from_environment()(
        {
            "experience_id": 17,
            "test_name": "empty input regression",
            "source_code": "assert 1 == 2",
        },
        None,
        FakeContext(),
    )

    assert result["passed"] is False
    assert result["experience_status"] == "failed"
    assert captured["status"] == "failed"
    assert captured["test_results"][-1]["passed"] is False


@pytest.mark.parametrize(
    "item,test_name",
    [
        (_experience(status="verified"), "empty input regression"),
        ({**_experience(), "source_evidence": []}, "empty input regression"),
        ({**_experience(), "test_plan": []}, "empty input regression"),
        (_experience(), "different test"),
    ],
)
def test_runner_rejects_unverifiable_or_unplanned_tests(monkeypatch, tmp_path, item, test_name):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: item)
    handler = experience_tasks.handler_from_environment()
    with pytest.raises(ValueError):
        handler(
            {"experience_id": 17, "test_name": test_name, "source_code": "assert True"},
            None,
            FakeContext(),
        )



@pytest.fixture
def experience_task_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    task_manager.initialize_task_store()
    experience.init_experience_tables()


def _persisted_experience():
    item = experience.create_experience(
        "Python", "An empty input should produce an empty result.", "Check the empty-input branch."
    )
    return experience.update_experience(
        item["id"],
        source_evidence=[{
            "url": "https://docs.python.org/3/library/",
            "claim": "Documented behavior",
            "excerpt": "Documented behavior",
        }],
        test_plan=[{"test": "empty input regression"}],
    )


def _install_experience_test_stubs(monkeypatch):
    monkeypatch.setattr(
        experience_tasks.web_sources,
        "fetch_public_document",
        lambda *args, **kwargs: SimpleNamespace(text="Documented behavior"),
    )
    monkeypatch.setattr(
        sandbox_tasks.sandbox,
        "run_python",
        lambda _source, **_kwargs: SandboxResult("succeeded", 0, "assertion passed\\n", "", 12),
    )


def _create_experience_test_task(item):
    return task_manager.create_task(
        experience_tasks.EXPERIENCE_TEST_TASK_TYPE,
        {
            "experience_id": item["id"],
            "test_name": "empty input regression",
            "source_code": "assert [] == list()",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        },
        max_attempts=1,
    )


def test_experience_test_and_durable_task_success_commit_atomically(
    experience_task_store, monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    _install_experience_test_stubs(monkeypatch)
    item = _persisted_experience()
    task = _create_experience_test_task(item)
    handler = experience_tasks.handler_from_environment()
    assert handler is not None

    worker = TaskWorker(
        "experience-atomic-worker",
        {experience_tasks.EXPERIENCE_TEST_TASK_TYPE: handler},
        lease_seconds=30,
    )
    assert worker.run_once() is True

    saved_task = task_manager.get_task(task["id"])
    saved_experience = experience.get_experience(item["id"])
    assert saved_task["status"] == "succeeded"
    assert saved_task["result"]["experience_version"] == saved_experience["version"]
    assert saved_experience["status"] == "verified"
    assert saved_experience["version"] == item["version"] + 1
    assert saved_experience["test_results"][-1]["passed"] is True
    assert any(
        version["version"] == item["version"]
        for version in experience.list_experience_versions(item["id"])
    )


def test_experience_pause_or_cancel_wins_before_atomic_domain_commit(
    experience_task_store, monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    _install_experience_test_stubs(monkeypatch)
    item = _persisted_experience()
    task = _create_experience_test_task(item)
    original = TaskContext.complete_with_commit

    def cancel_before_commit(context, checkpoint, operation):
        task_manager.request_cancel(context.task_id)
        return original(context, checkpoint, operation)

    monkeypatch.setattr(TaskContext, "complete_with_commit", cancel_before_commit)
    handler = experience_tasks.handler_from_environment()
    assert handler is not None
    worker = TaskWorker(
        "experience-cancel-worker",
        {experience_tasks.EXPERIENCE_TEST_TASK_TYPE: handler},
        lease_seconds=30,
    )
    assert worker.run_once() is True

    saved_task = task_manager.get_task(task["id"])
    saved_experience = experience.get_experience(item["id"])
    assert saved_task["status"] == "cancelled"
    assert saved_experience["version"] == item["version"]
    assert saved_experience["status"] == "identified"
    assert saved_experience["test_results"] == []


def test_experience_test_does_not_overwrite_a_concurrent_edit(
    experience_task_store, monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    item = _persisted_experience()

    def concurrent_edit(_source, **_kwargs):
        experience.update_experience(item["id"], takeaway="Concurrent edit")
        return SandboxResult("succeeded", 0, "assertion passed\\n", "", 12)

    monkeypatch.setattr(
        experience_tasks.web_sources,
        "fetch_public_document",
        lambda *args, **kwargs: SimpleNamespace(text="Documented behavior"),
    )
    monkeypatch.setattr(sandbox_tasks.sandbox, "run_python", concurrent_edit)
    task = _create_experience_test_task(item)
    handler = experience_tasks.handler_from_environment()
    assert handler is not None
    worker = TaskWorker(
        "experience-version-worker",
        {experience_tasks.EXPERIENCE_TEST_TASK_TYPE: handler},
        lease_seconds=30,
    )
    assert worker.run_once() is True

    saved_task = task_manager.get_task(task["id"])
    saved_experience = experience.get_experience(item["id"])
    assert saved_task["status"] == "failed"
    assert "changed while its test was running" in saved_task["error"]
    assert saved_experience["version"] == item["version"] + 1
    assert saved_experience["takeaway"] == "Concurrent edit"
    assert saved_experience["test_results"] == []
