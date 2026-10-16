import hashlib

import pytest

from app import sandbox_tasks
from app.sandbox import SandboxResult


class FakeContext:
    def __init__(self):
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
    return executable


def test_sandbox_handler_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    monkeypatch.delenv("SANDBOX_DOCKER_EXECUTABLE", raising=False)
    monkeypatch.delenv("SANDBOX_PYTHON_IMAGE", raising=False)
    assert sandbox_tasks.handler_from_environment() is None


@pytest.mark.parametrize("value", ["maybe", "enabled", "2"])
def test_sandbox_handler_rejects_ambiguous_enable_flag(monkeypatch, value):
    monkeypatch.setenv("SANDBOX_ENABLED", value)
    with pytest.raises(ValueError, match="SANDBOX_ENABLED"):
        sandbox_tasks.handler_from_environment()


def test_sandbox_handler_requires_absolute_existing_docker_and_digest_pinned_image(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SANDBOX_DOCKER_EXECUTABLE", "docker")
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:latest")
    with pytest.raises(ValueError, match="absolute file path"):
        sandbox_tasks.handler_from_environment()

    executable = _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("SANDBOX_PYTHON_IMAGE", "python:latest")
    with pytest.raises(ValueError, match="SHA-256 digest"):
        sandbox_tasks.handler_from_environment()
    assert executable.is_file()


def test_handler_records_hash_and_execution_evidence_without_storing_source_in_checkpoint(
    monkeypatch, tmp_path
):
    executable = _configure(monkeypatch, tmp_path)
    calls = []

    def fake_run(source, **kwargs):
        calls.append((source, kwargs))
        return SandboxResult("succeeded", 0, "42\\n", "", 17)

    monkeypatch.setattr(sandbox_tasks.sandbox, "run_python", fake_run)
    handler = sandbox_tasks.handler_from_environment()
    context = FakeContext()
    source = "print(6 * 7)"
    result = handler(
        {"source_code": source, "timeout_seconds": 5, "max_output_bytes": 1000},
        None,
        context,
    )

    assert result["execution_status"] == "succeeded"
    assert result["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert result["stdout"] == "42\\n"
    assert calls[0][0] == source
    assert calls[0][1]["docker_executable"] == executable
    assert calls[0][1]["image"].endswith("a" * 64)
    assert calls[0][1]["control_check"] == context.control_status
    assert len(context.checkpoints) == 2
    assert context.checkpoints[0]["phase"] == "sandbox_started"
    assert context.checkpoints[1]["phase"] == "sandbox_finished"
    assert "source_code" not in context.checkpoints[0]
    assert "source_code" not in result


def test_payload_cannot_override_image_or_docker_executable(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    handler = sandbox_tasks.handler_from_environment()
    with pytest.raises(ValueError, match="unsupported fields"):
        handler(
            {
                "source_code": "print('no')",
                "image": "attacker/image:latest",
                "docker_executable": "/tmp/fake",
            },
            None,
            FakeContext(),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"source_code": ""},
        {"source_code": "print(1)", "timeout_seconds": True},
        {"source_code": "print(1)", "timeout_seconds": 0},
        {"source_code": "print(1)", "max_output_bytes": 0},
    ],
)
def test_handler_rejects_invalid_payloads_before_checkpoint(monkeypatch, tmp_path, payload):
    _configure(monkeypatch, tmp_path)
    handler = sandbox_tasks.handler_from_environment()
    context = FakeContext()
    with pytest.raises(ValueError):
        handler(payload, None, context)
    assert context.checkpoints == []
