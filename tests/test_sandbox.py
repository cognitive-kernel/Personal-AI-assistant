import io
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app import sandbox, db, learning, learning_capabilities_routes, learning_practical


IMAGE = "python:3.12-slim@sha256:" + "a" * 64


class FakeProcess:
    def __init__(self, args, *, stdout=b"hello\n", stderr=b"", returncode=0):
        self.args = args
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def _configured_docker(tmp_path):
    executable = tmp_path / ("docker.exe" if __import__("os").name == "nt" else "docker")
    executable.write_text("placeholder", encoding="utf-8")
    return executable


def test_sandbox_runs_only_digest_pinned_python_in_no_network_container(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    calls = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(args)
        calls.append((args, kwargs, process))
        return process

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("KAGGLE_API_TOKEN", "must-not-enter-docker-cli-env")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    result = sandbox.run_python(
        "print('hello')",
        docker_executable=executable,
        image=IMAGE,
    )

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert len(calls) == 1
    args, kwargs, _ = calls[0]
    assert args[:2] == [str(executable), "run"]
    assert "--pull=never" in args
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--memory=256m" in args
    assert "--cpus=1" in args
    assert "--pids-limit=64" in args
    assert "--user=65534:65534" in args
    assert args[-4:] == ["python", "-I", "-S", "/workspace/main.py"]
    assert kwargs["shell"] is False
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert "KAGGLE_API_TOKEN" not in kwargs["env"]
    assert kwargs["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"


def test_sandbox_stdin_is_bounded_and_mounted_read_only(tmp_path, monkeypatch):
    from pathlib import Path

    executable = _configured_docker(tmp_path)
    captured = {}
    process = FakeProcess([], stdout=b"ok\\n")

    def fake_popen(args, **kwargs):
        mount = args[args.index("--mount") + 1]
        workspace = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
        captured["runner"] = (workspace / "main.py").read_text(encoding="utf-8")
        captured["learner"] = (workspace / "learner.py").read_text(encoding="utf-8")
        captured["input"] = (workspace / "input.txt").read_text(encoding="utf-8")
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    payload = '{"values":[2,4]}'
    code = "print('ok')"
    result = sandbox.run_python(
        code, docker_executable=executable, image=IMAGE, stdin_text=payload
    )

    assert result.status == "succeeded"
    assert captured["input"] == payload
    assert captured["learner"] == code
    assert "runpy.run_path('/workspace/learner.py'" in captured["runner"]
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
    assert "--network=none" in captured["args"]

    with pytest.raises(ValueError, match="16 KiB"):
        sandbox.run_python(
            "print(1)", docker_executable=executable, image=IMAGE,
            stdin_text="x" * (16 * 1024 + 1),
        )


def test_sandbox_returns_unavailable_without_docker_and_never_falls_back(tmp_path):
    result = sandbox.run_python(
        "print('should not run')",
        docker_executable=tmp_path / "missing-docker",
        image=IMAGE,
    )

    assert result.status == "unavailable"
    assert result.exit_code is None
    assert "docker executable is not available" in (result.reason or "").lower()


def test_sandbox_bounds_source_image_timeout_and_output():
    with pytest.raises(ValueError, match="non-empty"):
        sandbox.run_python("", docker_executable="/not-used", image=IMAGE)
    with pytest.raises(ValueError, match="NUL"):
        sandbox.run_python("print('x')\x00", docker_executable="/not-used", image=IMAGE)
    with pytest.raises(ValueError, match="64 KiB"):
        sandbox.run_python("x" * (64 * 1024 + 1), docker_executable="/not-used", image=IMAGE)
    with pytest.raises(ValueError, match="digest-pinned"):
        sandbox.run_python("print(1)", docker_executable="/not-used", image="python:3.12-slim")
    with pytest.raises(ValueError, match="timeout_seconds"):
        sandbox.run_python("print(1)", docker_executable="/not-used", image=IMAGE, timeout_seconds=True)
    with pytest.raises(ValueError, match="max_output_bytes"):
        sandbox.run_python("print(1)", docker_executable="/not-used", image=IMAGE, max_output_bytes=0)


def test_sandbox_caps_output_per_stream_and_reports_nonzero_exit(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    processes = iter(
        [
            FakeProcess([], stdout=b"123456789", stderr=b"", returncode=0),
            FakeProcess([], stdout=b"", stderr=b"traceback", returncode=2),
        ]
    )
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: next(processes))

    oversized = sandbox.run_python(
        "print('large')",
        docker_executable=executable,
        image=IMAGE,
        max_output_bytes=4,
    )
    failed = sandbox.run_python(
        "raise RuntimeError()",
        docker_executable=executable,
        image=IMAGE,
    )

    assert oversized.status == "output_limit_exceeded"
    assert oversized.stdout == "1234"
    assert failed.status == "failed"
    assert failed.exit_code == 2
    assert failed.stderr == "traceback"


def test_sandbox_terminates_container_client_after_timeout(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    process = FakeProcess([], stdout=b"", stderr=b"", returncode=None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)
    times = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: next(times))

    result = sandbox.run_python(
        "while True: pass",
        docker_executable=executable,
        image=IMAGE,
        timeout_seconds=1,
    )

    assert result.status == "timed_out"
    assert process.terminated



def test_sandbox_removes_temporary_source_workspace_after_exit(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    captured = {}

    def fake_popen(args, **kwargs):
        captured["mount"] = args[args.index("--mount") + 1]
        return FakeProcess(args, stdout=b"done", returncode=0)

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    result = sandbox.run_python(
        "print('done')",
        docker_executable=executable,
        image=IMAGE,
    )

    source = captured["mount"].split(",")[1].removeprefix("source=")
    assert result.status == "succeeded"
    assert not __import__("pathlib").Path(source).exists()



@pytest.mark.parametrize(
    "control,expected",
    [
        ("pause_requested", "paused"),
        ("cancel_requested", "cancelled"),
        ("unknown", "cancelled"),
    ],
)
def test_sandbox_stops_container_when_task_control_changes(tmp_path, monkeypatch, control, expected):
    executable = _configured_docker(tmp_path)
    process = FakeProcess([], stdout=b"", stderr=b"", returncode=None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)

    result = sandbox.run_python(
        "while True: pass",
        docker_executable=executable,
        image=IMAGE,
        control_check=lambda: control,
    )

    assert result.status == expected
    assert process.terminated
    assert "task" in (result.reason or "").lower() or "control" in (result.reason or "").lower()


def test_sandbox_stops_if_task_control_check_raises(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    process = FakeProcess([], stdout=b"", stderr=b"", returncode=None)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)

    def lost_lease():
        raise RuntimeError("lease lost")

    result = sandbox.run_python(
        "while True: pass",
        docker_executable=executable,
        image=IMAGE,
        control_check=lost_lease,
    )

    assert result.status == "cancelled"
    assert process.terminated
    assert "lease state was unavailable" in (result.reason or "")



def test_sandbox_container_is_labeled_with_task_id(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        return FakeProcess(args)

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    result = sandbox.run_python(
        "print('task')",
        docker_executable=executable,
        image=IMAGE,
        task_id=41,
    )

    assert result.status == "succeeded"
    assert "--label" in captured["args"]
    assert f"{sandbox._SANDBOX_LABEL}=true" in captured["args"]
    assert f"{sandbox._TASK_ID_LABEL}=41" in captured["args"]


def test_cleanup_removes_only_old_containers_for_confirmed_terminal_tasks(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    container_ids = ["a" * 12, "b" * 12, "c" * 12, "d" * 12]
    metadata = {
        container_ids[0]: ({sandbox._SANDBOX_LABEL: "true", sandbox._TASK_ID_LABEL: "7"}, "running", old),
        container_ids[1]: ({sandbox._SANDBOX_LABEL: "true", sandbox._TASK_ID_LABEL: "8"}, "running", old),
        container_ids[2]: ({sandbox._SANDBOX_LABEL: "true", sandbox._TASK_ID_LABEL: "9"}, "exited", recent),
        container_ids[3]: ({sandbox._SANDBOX_LABEL: "false", sandbox._TASK_ID_LABEL: "7"}, "exited", old),
    }
    removed = []

    def fake_run(args, **kwargs):
        if args[1:3] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(args, 0, "\n".join(container_ids), "")
        if args[1] == "inspect":
            labels, state, created = metadata[args[-1]]
            return subprocess.CompletedProcess(
                args, 0, f"{json.dumps(labels)}|{state}|{created}", ""
            )
        if args[1] == "rm":
            removed.append(args[-1])
            return subprocess.CompletedProcess(args, 0, args[-1], "")
        raise AssertionError(f"Unexpected Docker command: {args[1:]}")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = sandbox.cleanup_orphaned_containers(
        docker_executable=executable,
        terminal_task_ids={7, 9},
        min_age_seconds=60,
    )

    assert result.removed == (container_ids[0],)
    assert result.skipped == 3
    assert result.errors == ()
    assert removed == [container_ids[0]]


def test_cleanup_fails_closed_when_docker_listing_fails(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "daemon unavailable")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    result = sandbox.cleanup_orphaned_containers(
        docker_executable=executable,
        terminal_task_ids={1},
    )

    assert result.removed == ()
    assert result.errors == ("Docker could not list sandbox containers.",)


def test_cleanup_rejects_invalid_task_ids_before_running_docker(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    called = []
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *args, **kwargs: called.append(args))

    with pytest.raises(ValueError, match="positive integer"):
        sandbox.cleanup_orphaned_containers(
            docker_executable=executable,
            terminal_task_ids={True},
        )
    assert called == []



def test_workspace_syntax_check_uses_readonly_no_network_docker(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    workspace = tmp_path / "project"
    (workspace / "app").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "app" / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / "tests" / "test_sample.py").write_text("assert True\n", encoding="utf-8")
    calls = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(
            args,
            stdout=b'{"status":"passed","files_checked":2}\n',
            stderr=b"",
            returncode=0,
        )
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    result = sandbox.run_workspace_syntax_check(
        workspace,
        docker_executable=executable,
        image=IMAGE,
        timeout_seconds=30,
    )

    assert result.status == "succeeded"
    args, kwargs = calls[0]
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--pull=never" in args
    assert args[-3:-1] == ["-S", "-c"]
    assert "ast.parse" in args[-1]
    assert kwargs["shell"] is False
    assert any("target=/workspace,readonly" in value for value in args)


def test_workspace_syntax_check_fails_closed_without_project_workspace(tmp_path):
    executable = _configured_docker(tmp_path)
    with pytest.raises(ValueError, match="app/ and tests/"):
        sandbox.run_workspace_syntax_check(
            tmp_path,
            docker_executable=executable,
            image=IMAGE,
        )



def test_workspace_tests_run_only_in_pinned_no_network_container(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    workspace = tmp_path / "candidate"
    (workspace / "app").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "app" / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / "tests" / "test_sample.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    calls = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(args, stdout=b"5 passed in 0.2s\n")
        calls.append((args, kwargs, process))
        return process

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    image = "sha256:" + "c" * 64
    result = sandbox.run_workspace_tests(
        workspace,
        docker_executable=executable,
        image=image,
    )

    assert result.status == "succeeded"
    args, kwargs, _ = calls[0]
    assert "--pull=never" in args
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--memory=2g" in args
    assert "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=768m,uid=65534,gid=65534" in args
    assert "--cpus=2" in args
    assert "--pids-limit=128" in args
    assert "--user=65534:65534" in args
    assert "type=bind,source=" + str(workspace.resolve()) + ",target=/workspace,readonly" in args
    assert args[-5] == image
    assert args[-4:-1] == ["python", "-I", "-c"]
    assert "pytest.main" in args[-1]
    assert kwargs["shell"] is False
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_workspace_tests_reject_mutable_or_unavailable_test_images(tmp_path, monkeypatch):
    workspace = tmp_path / "candidate"
    (workspace / "app").mkdir(parents=True)
    (workspace / "tests").mkdir()
    with pytest.raises(ValueError, match="pinned by sha256"):
        sandbox.run_workspace_tests(
            workspace,
            docker_executable="/not-used",
            image="python:3.11-slim",
        )
    result = sandbox.run_workspace_tests(
        workspace,
        docker_executable=tmp_path / "missing-docker",
        image="sha256:" + "d" * 64,
    )
    assert result.status == "unavailable"



def test_workspace_tests_report_failure_without_host_fallback(tmp_path, monkeypatch):
    executable = _configured_docker(tmp_path)
    workspace = tmp_path / "candidate"
    (workspace / "app").mkdir(parents=True)
    (workspace / "tests").mkdir()
    process = FakeProcess([], stdout=b"1 failed", stderr=b"assertion failed", returncode=1)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *args, **kwargs: process)

    result = sandbox.run_workspace_tests(
        workspace,
        docker_executable=executable,
        image="sha256:" + "e" * 64,
    )

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.stdout == "1 failed"
    assert result.stderr == "assertion failed"



@pytest.mark.parametrize(
    ("image", "language", "expected"),
    [
        ("node:22-bookworm-slim@sha256:" + "a" * 64, "javascript", True),
        ("rust:1.84-slim@sha256:" + "b" * 64, "rust", True),
        ("node:latest", "javascript", False),
        ("python:3.12-slim@sha256:" + "a" * 64, "javascript", False),
        ("rust:latest", "rust", False),
    ],
)
def test_language_runner_requires_official_digest_pinned_images(image, language, expected):
    from app.sandbox import is_valid_language_image_reference

    assert is_valid_language_image_reference(image, language) is expected


@pytest.mark.parametrize(
    ("language", "image", "stdout", "required_tmpfs"),
    [
        (
            "javascript", "node:22-bookworm-slim@sha256:" + "b" * 64,
            b"__PAI_NODE_VERSION__:22.14.0\\n", "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65534,gid=65534",
        ),
        (
            "rust", "rust:1.84-slim@sha256:" + "c" * 64,
            b"__PAI_RUST_VERSION__:1.84.0\\n", "--tmpfs=/tmp:rw,nosuid,nodev,exec,size=128m,uid=65534,gid=65534",
        ),
    ],
)
def test_language_sandbox_is_digest_pinned_no_network_and_resource_bounded(
    tmp_path, monkeypatch, language, image, stdout, required_tmpfs
):
    executable = _configured_docker(tmp_path)
    calls = []
    monkeypatch.setattr(
        sandbox.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or FakeProcess(args, stdout=stdout),
    )

    result = sandbox.run_language(
        "assert.equal(1, 1);" if language == "javascript" else "assert_eq!(1, 1);",
        language=language,
        docker_executable=executable,
        image=image,
        timeout_seconds=5,
        max_output_bytes=1000,
        task_id=19,
    )

    assert result.status == "succeeded"
    args = calls[0][0]
    assert "--pull=never" in args
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--pids-limit=64" in args
    assert "--cpus=1" in args
    assert required_tmpfs in args
    assert f"{sandbox._TASK_ID_LABEL}=19" in args
    assert image in args
    if language == "rust":
        assert "--memory=512m" in args
        assert any(arg == "rustc" or "rustc --edition=2021" in arg for arg in args)
    else:
        assert "--memory=256m" in args
        assert "node" in args



def test_practical_catalog_hides_expected_outputs():
    items = learning_practical.list_exercises()
    assert len(items) == 3
    assert all("expected" not in str(item) for item in items)


def test_practical_grader_rejects_failed_execution():
    case = learning_practical.get_test_cases(learning_practical.EXERCISE_ID)[0]
    assert not learning_practical.grade_case_output(
        learning_practical.EXERCISE_ID, case["id"], "failed", 1, "6"
    )


def test_practical_submit_requires_explicit_sandbox_consent(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Python JSON processing")
    with db.learning_connection() as conn:
        conn.execute("UPDATE learning_jobs SET status='completed' WHERE id=?", (job["id"],))
    monkeypatch.setattr(learning_capabilities_routes.sandbox_consent, "get_consent", lambda: {"enabled": False})
    monkeypatch.setattr(learning_capabilities_routes, "_practical_sandbox_available", lambda: True)
    with pytest.raises(Exception) as exc:
        learning_capabilities_routes.submit_practical_exercise(
            "python-json-sum-v1",
            learning_capabilities_routes.PracticalSubmission(job_id=job["id"], source_code="print(1)"),
        )
    assert getattr(exc.value, "status_code", None) == 409
