from types import SimpleNamespace

import pytest

from app import experience_tasks, sandbox_tasks
from app.sandbox import SandboxResult


class FakeContext:
    def __init__(self):
        self.task_id = 501
        self.checkpoints = []
        self.status = "running"

    def checkpoint(self, value):
        self.checkpoints.append(value)
        return {"status": "running", "checkpoint": value}

    def control_status(self):
        return self.status


def _configure(monkeypatch, tmp_path):
    executable = tmp_path / "docker"
    executable.write_text("test executable", encoding="utf-8")
    monkeypatch.setenv("SANDBOX_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", str(executable))
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:3.12-slim@sha256:" + "a" * 64)
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: True)


def _patch_live_source(monkeypatch, text="The page says: Documented behavior for empty input."):
    monkeypatch.setattr(
        experience_tasks.web_sources,
        "fetch_public_document",
        lambda *_args, **_kwargs: SimpleNamespace(text=text),
    )


def _experience(status="identified"):
    return {
        "id": 17,
        "topic": "Python",
        "problem": "An empty input should produce an empty result.",
        "hypothesis": "The empty-input branch is missing.",
        "status": status,
        "version": 1,
        "source_evidence": [{"url": "https://docs.python.org/3/library/", "claim": "Documented behavior", "excerpt": "Documented behavior for empty input."}],
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
    _patch_live_source(monkeypatch)
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: _experience())
    captured = {}

    def fake_update(experience_id, **changes):
        captured.update(experience_id=experience_id, **changes)
        return {"id": experience_id, "version": 2, **changes}

    monkeypatch.setattr(experience_tasks.experience, "update_experience", fake_update)
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
    assert captured["test_results"][-1]["passed"] is True
    assert captured["test_results"][-1]["runner"] == "docker-sandbox"
    assert captured["test_results"][-1]["source_sha256"]
    assert captured["source_evidence"][0]["live_content_sha256"]
    assert captured["test_results"][-1]["source_evidence"][0]["excerpt_sha256"]
    assert "source_code" not in captured["test_results"][-1]


def test_controlled_runner_records_failure_without_verification(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _patch_live_source(monkeypatch)
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: _experience())
    captured = {}

    def fake_update(experience_id, **changes):
        captured.update(experience_id=experience_id, **changes)
        return {"id": experience_id, "version": 2, **changes}

    monkeypatch.setattr(experience_tasks.experience, "update_experience", fake_update)
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
    _patch_live_source(monkeypatch)
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: item)
    handler = experience_tasks.handler_from_environment()
    with pytest.raises(ValueError):
        handler(
            {"experience_id": 17, "test_name": test_name, "source_code": "assert True"},
            None,
            FakeContext(),
        )


def test_runner_rejects_excerpt_missing_from_live_page_before_sandbox(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _patch_live_source(monkeypatch, "The page contains different text.")
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: _experience())
    sandbox_calls = []
    monkeypatch.setattr(sandbox_tasks.sandbox, "run_python", lambda *_a, **_k: sandbox_calls.append(True))
    handler = experience_tasks.handler_from_environment()
    with pytest.raises(ValueError, match="absent from the current live source"):
        handler(
            {"experience_id": 17, "test_name": "empty input regression", "source_code": "assert True"},
            None,
            FakeContext(),
        )
    assert sandbox_calls == []


def test_runner_rejects_non_official_source_host_before_fetch(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    item = _experience()
    item["source_evidence"][0]["url"] = "https://example.com/page"
    monkeypatch.setattr(experience_tasks.experience, "get_experience", lambda _id: item)
    fetch_calls = []
    monkeypatch.setattr(experience_tasks.web_sources, "fetch_public_document", lambda *a, **k: fetch_calls.append(a))
    handler = experience_tasks.handler_from_environment()
    with pytest.raises(ValueError, match="allow-listed official HTTPS"):
        handler({"experience_id": 17, "test_name": "empty input regression", "source_code": "assert True"}, None, FakeContext())
    assert fetch_calls == []
