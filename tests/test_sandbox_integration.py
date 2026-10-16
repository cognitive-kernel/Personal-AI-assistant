"""Real-Docker integration tests; run only with an explicitly supplied pinned image."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app import sandbox

pytestmark = pytest.mark.sandbox_integration
_INTEGRATION_TASK_ID = 2147483000
_IMAGE_PATTERN = re.compile(
    r"^(?:python|docker\.io/library/python):[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$"
)


@pytest.fixture
def sandbox_config():
    if os.getenv("RUN_SANDBOX_INTEGRATION") != "1":
        pytest.skip("Set RUN_SANDBOX_INTEGRATION=1 to run real-Docker integration tests.")
    docker_value = os.getenv("SANDBOX_DOCKER_EXECUTABLE") or shutil.which("docker")
    image = os.getenv("SANDBOX_PYTHON_IMAGE", "").strip()
    if not docker_value:
        pytest.skip("Docker executable is unavailable.")
    docker = Path(docker_value).expanduser()
    if not docker.is_absolute() or not docker.is_file():
        pytest.skip("SANDBOX_DOCKER_EXECUTABLE must be an existing absolute path.")
    if not _IMAGE_PATTERN.fullmatch(image):
        pytest.fail("SANDBOX_PYTHON_IMAGE must be an official Python image pinned by SHA-256 digest.")
    inspected = subprocess.run(
        [str(docker), "image", "inspect", image],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if inspected.returncode != 0:
        pytest.skip("The digest-pinned Python image is not preloaded; pull it before running this suite.")
    yield docker, image
    cleanup = sandbox.cleanup_orphaned_containers(
        docker_executable=docker,
        terminal_task_ids={_INTEGRATION_TASK_ID},
        min_age_seconds=0,
    )
    if cleanup.errors:
        pytest.fail("Sandbox integration cleanup failed: " + "; ".join(cleanup.errors))


def _run(config, source: str, *, timeout: int = 10, output_limit: int = 4096):
    docker, image = config
    return sandbox.run_python(
        source,
        docker_executable=docker,
        image=image,
        timeout_seconds=timeout,
        max_output_bytes=output_limit,
        task_id=_INTEGRATION_TASK_ID,
    )


def test_real_container_enforces_non_root_read_only_mount_and_network_isolation(sandbox_config):
    source = """
import os
import socket
from pathlib import Path

assert os.geteuid() != 0
try:
    Path('/workspace/main.py').write_text('overwrite')
except OSError:
    pass
else:
    raise AssertionError('source mount is writable')
Path('/tmp/sandbox-write-probe').write_text('tmpfs-ok')
assert Path('/tmp/sandbox-write-probe').read_text() == 'tmpfs-ok'
blocked = False
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    blocked = True
assert blocked, 'network unexpectedly reachable'
print('isolation-ok')
"""
    result = _run(sandbox_config, source)
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert "isolation-ok" in result.stdout


def test_real_container_has_expected_docker_security_configuration(sandbox_config):
    docker, image = sandbox_config
    results = {}

    def run():
        results["result"] = _run(
            sandbox_config,
            "import time\ntime.sleep(4)\nprint('inspection-window')",
            timeout=12,
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        container_id = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and thread.is_alive():
            listing = subprocess.run(
                [
                    str(docker), "ps", "-q",
                    "--filter", "label=com.cognitivekernel.personal-ai-assistant.sandbox=true",
                    "--filter", f"label=com.cognitivekernel.personal-ai-assistant.task-id={_INTEGRATION_TASK_ID}",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if listing.returncode == 0 and listing.stdout.strip():
                container_id = listing.stdout.strip().splitlines()[0]
                break
            time.sleep(0.05)
        assert container_id, "sandbox container did not appear for inspection"

        inspected = subprocess.run(
            [str(docker), "inspect", container_id],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert inspected.returncode == 0, inspected.stderr
        record = json.loads(inspected.stdout)[0]
        host = record["HostConfig"]
        config = record["Config"]
        mounts = record["Mounts"]
        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert "ALL" in (host.get("CapDrop") or [])
        assert host["Memory"] == 256 * 1024 * 1024
        assert host["PidsLimit"] == 64
        assert "no-new-privileges" in " ".join(host.get("SecurityOpt") or [])
        assert config["User"] == "65534:65534"
        workspace_mount = next(mount for mount in mounts if mount.get("Destination") == "/workspace")
        assert workspace_mount["RW"] is False
        assert image in config.get("Image", "") or record.get("Config", {}).get("Image") == image
    finally:
        thread.join(timeout=15)

    assert not thread.is_alive(), "sandbox integration worker did not finish"
    assert results["result"].status == "succeeded"
    assert results["result"].stdout.strip() == "inspection-window"


def test_real_container_timeout_and_output_limit_are_reported(sandbox_config):
    timed_out = _run(sandbox_config, "import time\ntime.sleep(5)", timeout=1)
    assert timed_out.status == "timed_out"

    overflow = _run(sandbox_config, "print('x' * 10000)", timeout=10, output_limit=100)
    assert overflow.status == "output_limit_exceeded"


def test_missing_docker_never_falls_back_to_host_execution(tmp_path):
    result = sandbox.run_python(
        "assert True",
        docker_executable=tmp_path / "missing-docker",
        image="python:3.12-slim@sha256:" + "a" * 64,
    )
    assert result.status == "unavailable"
    assert result.exit_code is None



def test_real_container_syntax_checks_workspace_without_executing_candidate(sandbox_config, tmp_path):
    docker, image = sandbox_config
    workspace = tmp_path / "candidate"
    (workspace / "app").mkdir(parents=True)
    (workspace / "tests").mkdir()
    marker = tmp_path / "candidate-was-executed"
    (workspace / "app" / "safe.py").write_text(
        "from pathlib import Path\\nPath(" + repr(str(marker)) + ").write_text('executed')\\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_safe.py").write_text("def test_safe():\\n    assert True\\n", encoding="utf-8")

    result = sandbox.run_workspace_syntax_check(
        workspace,
        docker_executable=docker,
        image=image,
        timeout_seconds=30,
    )
    assert result.status == "succeeded", result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {"status": "passed", "files_checked": 2}
    assert not marker.exists()

    (workspace / "app" / "broken.py").write_text("def broken(:\\n", encoding="utf-8")
    invalid = sandbox.run_workspace_syntax_check(
        workspace,
        docker_executable=docker,
        image=image,
        timeout_seconds=30,
    )
    assert invalid.status == "failed"
    assert "SyntaxError" in invalid.stdout
    assert not marker.exists()
