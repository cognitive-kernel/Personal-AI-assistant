import hashlib

import pytest

from app import learning_practical, sandbox_tasks
from app.sandbox import SandboxResult


class FakeContext:
    def __init__(self):
        self.task_id = 123
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
    monkeypatch.setenv(
        "SANDBOX_PYTHON_IMAGE", "docker.io/library/python:3.12-slim@sha256:" + "a" * 64
    )
    assert sandbox_tasks.handler_from_environment() is not None
    assert executable.is_file()


def test_handler_records_hash_and_execution_evidence_without_storing_source_in_checkpoint(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: True)
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
    assert callable(calls[0][1]["control_check"])
    assert calls[0][1]["control_check"]() == "running"
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



def test_handler_refuses_execution_without_explicit_consent(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: False)
    handler = sandbox_tasks.handler_from_environment()
    context = FakeContext()
    with pytest.raises(ValueError, match="explicit, current user consent"):
        handler({"source_code": "print('blocked')"}, None, context)
    assert context.checkpoints == []



def test_revoking_consent_stops_running_sandbox_and_records_checkpoint(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    consent_states = iter([True, True, False])
    monkeypatch.setattr(
        sandbox_tasks.sandbox_consent,
        "is_enabled",
        lambda: next(consent_states),
    )
    clock = iter([10.0, 11.0])
    monkeypatch.setattr(sandbox_tasks.time, "monotonic", lambda: next(clock))

    def fake_run(source, **kwargs):
        control_check = kwargs["control_check"]
        assert control_check() == "running"
        assert control_check() == "control_unavailable"
        return SandboxResult(
            "cancelled", -15, "", "", 50,
            "Sandbox stopped because task control/lease state was unavailable.",
        )

    monkeypatch.setattr(sandbox_tasks.sandbox, "run_python", fake_run)
    handler = sandbox_tasks.handler_from_environment()
    context = FakeContext()

    with pytest.raises(RuntimeError, match="consent was revoked"):
        handler({"source_code": "print('long task')"}, None, context)

    assert len(context.checkpoints) == 2
    assert context.checkpoints[-1]["execution_status"] == "cancelled"
    assert context.checkpoints[-1]["source_sha256"] == hashlib.sha256(
        b"print('long task')"
    ).hexdigest()



def test_terminal_container_cleanup_uses_only_durable_terminal_task_ids(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    captured = {}

    terminal_query = {}

    def fake_terminal_ids(*, limit, min_terminal_age_seconds):
        terminal_query["limit"] = limit
        terminal_query["min_terminal_age_seconds"] = min_terminal_age_seconds
        return [12, 18]

    monkeypatch.setattr(sandbox_tasks.task_manager, "list_terminal_task_ids", fake_terminal_ids)

    def fake_cleanup(**kwargs):
        captured.update(kwargs)
        return sandbox_tasks.sandbox.SandboxCleanupResult((), 2, ())

    monkeypatch.setattr(sandbox_tasks.sandbox, "cleanup_orphaned_containers", fake_cleanup)
    sandbox_tasks.cleanup_terminal_containers()

    assert captured["terminal_task_ids"] == {12, 18}
    assert captured["min_age_seconds"] == 60
    assert terminal_query == {"limit": 1000, "min_terminal_age_seconds": 60}


def test_terminal_container_cleanup_is_disabled_when_sandbox_is_off(
    monkeypatch, tmp_path
):
    executable = _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("SANDBOX_ENABLED", "false")
    called = []
    monkeypatch.setattr(
        sandbox_tasks.sandbox, "cleanup_orphaned_containers",
        lambda **kwargs: called.append(kwargs),
    )

    sandbox_tasks.cleanup_terminal_containers()
    assert executable.is_file()
    assert called == []


@pytest.mark.parametrize(
    ("language", "env_name", "image", "runner_name"),
    [
        ("javascript", "SANDBOX_NODE_IMAGE", "node:22-bookworm-slim@sha256:" + "b" * 64, "run_language"),
        ("rust", "SANDBOX_RUST_IMAGE", "rust:1.84-slim@sha256:" + "c" * 64, "run_language"),
    ],
)
def test_language_runner_uses_only_configured_digest_pinned_image(
    monkeypatch, tmp_path, language, env_name, image, runner_name
):
    executable = _configure(monkeypatch, tmp_path)
    monkeypatch.setenv(env_name, image)
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: True)
    captured = {}

    def fake_run(source, **kwargs):
        captured.update(source=source, **kwargs)
        return SandboxResult("succeeded", 0, "ok", "", 5)

    monkeypatch.setattr(sandbox_tasks.sandbox, runner_name, fake_run)
    handler = sandbox_tasks.handler_from_environment(allow_language_runners=True)
    context = FakeContext()
    result = handler(
        {
            "language": language,
            "source_code": "assertion harness",
            "timeout_seconds": 5,
            "max_output_bytes": 1000,
        },
        None,
        context,
    )

    assert result["execution_status"] == "succeeded"
    assert captured["language"] == language
    assert captured["image"] == image
    assert captured["docker_executable"] == executable
    assert captured["task_id"] == context.task_id
    assert context.checkpoints[0]["language"] == language


def test_language_runner_requires_image_and_is_not_enabled_by_default(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    handler = sandbox_tasks.handler_from_environment(allow_language_runners=True)
    with pytest.raises(ValueError, match="SANDBOX_NODE_IMAGE"):
        handler(
            {"language": "javascript", "source_code": "assert.equal(1, 1)"},
            None,
            FakeContext(),
        )

    default_handler = sandbox_tasks.handler_from_environment()
    with pytest.raises(ValueError, match="unsupported fields"):
        default_handler(
            {"language": "javascript", "source_code": "assert.equal(1, 1)"},
            None,
            FakeContext(),
        )


def test_practical_sandbox_handler_uses_stable_task_idempotency_key(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(sandbox_tasks.sandbox_consent, "is_enabled", lambda: True)
    monkeypatch.setattr(
        learning_practical, "build_grader_source", lambda exercise_id, source: "print('grader')"
    )
    monkeypatch.setattr(learning_practical, "grade_case_output", lambda *args: True)
    monkeypatch.setattr(
        sandbox_tasks.sandbox,
        "run_python",
        lambda *args, **kwargs: SandboxResult("succeeded", 0, "ignored", "", 1),
    )
    captured = {}
    expected = {"attempt_id": 9, "score": 100.0, "idempotent_replay": False}

    def fake_record(job_id, exercise_id, report, source_code, *, idempotency_key=None):
        captured.update(
            job_id=job_id,
            exercise_id=exercise_id,
            report=report,
            source_code=source_code,
            idempotency_key=idempotency_key,
        )
        return expected

    monkeypatch.setattr(learning_practical, "record_practical_attempt", fake_record)
    handler = sandbox_tasks.handler_from_environment()
    context = FakeContext()
    result = handler(
        {
            "source_code": "print(1 + 2)",
            "learning_practical": {
                "exercise_id": learning_practical.EXERCISE_ID,
                "job_id": 7,
            },
        },
        None,
        context,
    )

    assert captured["job_id"] == 7
    assert captured["idempotency_key"] == f"sandbox-practical-task:{context.task_id}"
    assert result["practical_assessment"] == expected
