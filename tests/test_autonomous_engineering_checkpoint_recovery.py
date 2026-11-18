from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import autonomous_engineering_checkpoint as checkpoint


def test_checkpoint_git_uses_bounded_process_tree_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return checkpoint.subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(checkpoint, "ROOT", tmp_path)
    monkeypatch.setattr(checkpoint, "run_command", fake_run)

    result = checkpoint.git("status", "--short", check=False, env={"GIT_OPTIONAL_LOCKS": "0"})

    assert result.returncode == 0
    assert calls == [(
        ["git", "status", "--short"],
        {
            "cwd": tmp_path,
            "env": {"GIT_OPTIONAL_LOCKS": "0"},
            "capture": True,
            "timeout_seconds": checkpoint.GIT_COMMAND_TIMEOUT_SECONDS,
        },
    )]


def _state(tmp_path: Path) -> tuple[dict[str, object], dict[str, Path]]:
    paths = {
        "directory": tmp_path,
        "patch": tmp_path / "checkpoint.patch",
        "metadata": tmp_path / "checkpoint.json",
        "plan": tmp_path / "plan.json",
        "stop": tmp_path / "checkpoint.stop",
    }
    paths["patch"].write_bytes(b"preserve this patch")
    metadata = {
        "schema_version": 4,
        "run_id": "run-9",
        "task": 2,
        "base_sha": "a" * 40,
        "watcher_pid": 4321,
        "watcher_started_at": 1234.5,
        "watcher_ready": True,
        "updated_at": 1.0,
        "patch_bytes": len(b"preserve this patch"),
    }
    paths["metadata"].write_text(json.dumps(metadata), encoding="utf-8")
    return metadata, paths


def test_restore_preserves_checkpoint_when_watcher_ownership_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    metadata, paths = _state(tmp_path)
    monkeypatch.setattr(checkpoint, "_latest_checkpoint", lambda: (metadata, paths))

    def refuse_stop(_metadata):
        raise RuntimeError("access denied; watcher may still be writing")

    monkeypatch.setattr(checkpoint, "stop_existing_watcher", refuse_stop)

    with pytest.raises(RuntimeError, match="access denied"):
        checkpoint.restore("a" * 40)

    assert paths["patch"].read_bytes() == b"preserve this patch"
    assert paths["metadata"].is_file()
    assert not paths["stop"].exists()


def test_clear_preserves_all_checkpoint_files_when_watcher_stop_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    metadata, paths = _state(tmp_path)
    monkeypatch.setattr(checkpoint, "paths", lambda run_id, task: paths)
    monkeypatch.setattr(checkpoint, "read_metadata", lambda run_id, task, **kwargs: metadata)

    def refuse_stop(_metadata):
        raise RuntimeError("watcher ownership is ambiguous")

    monkeypatch.setattr(checkpoint, "stop_existing_watcher", refuse_stop)

    with pytest.raises(RuntimeError, match="ownership is ambiguous"):
        checkpoint.clear("run-9", 2)

    assert paths["patch"].read_bytes() == b"preserve this patch"
    assert paths["metadata"].is_file()
    assert not paths["stop"].exists()


def test_watcher_start_does_not_replace_state_when_previous_owner_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    metadata, paths = _state(tmp_path)
    ready = tmp_path / "checkpoint.ready"
    error = tmp_path / "checkpoint.error"
    ready.write_text("old readiness", encoding="utf-8")
    error.write_text("old error", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "paths", lambda run_id, task: paths)
    monkeypatch.setattr(checkpoint, "watcher_marker_paths", lambda run_id, task, **kwargs: (ready, error))
    monkeypatch.setattr(checkpoint, "read_metadata", lambda run_id, task, **kwargs: metadata)

    def refuse_stop(_metadata):
        raise RuntimeError("previous watcher is not verifiably stopped")

    monkeypatch.setattr(checkpoint, "stop_existing_watcher", refuse_stop)

    with pytest.raises(RuntimeError, match="not verifiably stopped"):
        checkpoint.watch("a" * 40, "run-9", 2, 5)

    assert paths["patch"].read_bytes() == b"preserve this patch"
    assert ready.read_text(encoding="utf-8") == "old readiness"
    assert error.read_text(encoding="utf-8") == "old error"


def test_checkpoint_watcher_real_process_stops_before_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared_state_tmp_path: Path,
):
    import json
    import os
    import subprocess
    import sys
    import time

    root = Path(__file__).resolve().parents[1]
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    program_data = shared_state_tmp_path / "ProgramData"
    state = program_data / "PersonalAIAssistant" / "autonomous-engineering"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    env = os.environ.copy()
    env.pop("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", None)
    env["PROGRAMDATA"] = str(program_data)
    executable = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen(
        [
            executable,
            str(root / "scripts" / "autonomous_engineering_checkpoint.py"),
            "watch",
            "--base-sha", base_sha,
            "--run-id", f"real-stop-{os.getpid()}",
            "--task", "1",
            "--interval", "5",
        ],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    metadata_path = state / f"worktree-checkpoint-real-stop-{os.getpid()}-1.json"
    try:
        deadline = time.monotonic() + 20
        metadata = None
        while time.monotonic() < deadline and process.poll() is None:
            if metadata_path.is_file():
                try:
                    candidate = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    candidate = None
                if isinstance(candidate, dict) and candidate.get("watcher_ready") is True:
                    metadata = candidate
                    break
            time.sleep(0.1)
        assert metadata is not None, (
            process.stdout.read() if process.poll() is not None else "watcher readiness timed out"
        )
        checkpoint.stop_existing_watcher(metadata)
        process.wait(timeout=10)
        assert process.poll() is not None
        assert metadata_path.is_file()
        assert checkpoint.paths(f"real-stop-{os.getpid()}", 1)["patch"].is_file()
        assert checkpoint.restore(base_sha) is False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_malformed_metadata_blocks_watcher_start_without_overwriting_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _, paths = _state(tmp_path)
    paths["metadata"].write_text("{not-json", encoding="utf-8")
    ready = tmp_path / "checkpoint.ready"
    error = tmp_path / "checkpoint.error"
    ready.write_text("existing-ready", encoding="utf-8")
    error.write_text("existing-error", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "paths", lambda run_id, task: paths)
    monkeypatch.setattr(checkpoint, "watcher_marker_paths", lambda run_id, task, **kwargs: (ready, error))

    with pytest.raises(RuntimeError, match="metadata is malformed"):
        checkpoint.watch("a" * 40, "run-9", 2, 5)

    assert paths["patch"].read_bytes() == b"preserve this patch"
    assert ready.read_text(encoding="utf-8") == "existing-ready"
    assert error.read_text(encoding="utf-8") == "existing-error"


def test_read_metadata_distinguishes_absent_from_corrupt_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _, paths = _state(tmp_path)
    monkeypatch.setattr(checkpoint, "paths", lambda run_id, task: paths)

    assert checkpoint.read_metadata("run-9", 2)["run_id"] == "run-9"
    paths["metadata"].write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid shape"):
        checkpoint.read_metadata("run-9", 2)


def test_unreadable_metadata_blocks_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, paths = _state(tmp_path)
    original_read_text = Path.read_text

    def denied(path: Path, *args, **kwargs):
        if path == paths["metadata"]:
            raise PermissionError("simulated access denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(checkpoint, "paths", lambda run_id, task: paths)

    with pytest.raises(RuntimeError, match="metadata cannot be read"):
        checkpoint.read_metadata("run-9", 2)


def test_checkpoint_store_lock_is_exclusive_across_processes_and_recovers_after_crash(
    tmp_path: Path,
):
    import os
    import subprocess
    import sys
    import time

    root = Path(__file__).resolve().parents[1]
    code = (
        "import os, time; from pathlib import Path; "
        "from scripts.autonomous_engineering_checkpoint_lock import checkpoint_store_lock; "
        f"with_lock=checkpoint_store_lock(Path({str(tmp_path)!r})); "
        "with_lock.__enter__(); time.sleep(1.0); with_lock.__exit__(None,None,None)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with checkpoint.checkpoint_store_lock(tmp_path, timeout_seconds=0):
                    pass
            except TimeoutError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("A second process acquired checkpoint ownership while the watcher held it.")
    finally:
        child.wait(timeout=10)

    with checkpoint.checkpoint_store_lock(tmp_path, timeout_seconds=1):
        pass


def test_checkpoint_watcher_rejects_duplicate_owner_before_mutating_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    import subprocess
    import sys
    import time

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    state = checkpoint.paths("duplicate-run", 1)
    state["patch"].write_bytes(b"existing checkpoint")
    state["metadata"].write_text(
        '{"run_id":"duplicate-run","task":1,"base_sha":"' + "a" * 40 + '"}',
        encoding="utf-8",
    )
    code = (
        "import time; from pathlib import Path; "
        "from scripts.autonomous_engineering_checkpoint_lock import checkpoint_store_lock; "
        f"lock=checkpoint_store_lock(Path({str(tmp_path)!r})); "
        "lock.__enter__(); time.sleep(2.0); lock.__exit__(None,None,None)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with checkpoint.checkpoint_store_lock(tmp_path, timeout_seconds=0):
                    pass
            except TimeoutError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("Test owner did not acquire checkpoint lock.")

        with pytest.raises(TimeoutError):
            checkpoint.watch("a" * 40, "duplicate-run", 1, 5)
        assert state["patch"].read_bytes() == b"existing checkpoint"
    finally:
        child.wait(timeout=10)


@pytest.mark.parametrize(("schema", "include_patch_bytes"), [(2, False), (3, True), (4, True)])
def test_latest_checkpoint_reads_supported_v2_v3_v4_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    schema: int,
    include_patch_bytes: bool,
):
    import json

    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    state = checkpoint.paths(f"schema-{schema}", 1)
    state["patch"].write_bytes(b"legacy-compatible patch")
    metadata = {
        "schema_version": schema,
        "run_id": f"schema-{schema}",
        "task": 1,
        "base_sha": "a" * 40,
        "updated_at": float(schema),
        "watcher_pid": None,
        "watcher_ready": False,
    }
    if include_patch_bytes:
        metadata["patch_bytes"] = len(b"legacy-compatible patch")
    state["metadata"].write_text(json.dumps(metadata), encoding="utf-8")

    latest = checkpoint._latest_checkpoint()

    assert latest is not None
    assert latest[0]["schema_version"] == schema
    assert latest[1]["patch"].read_bytes() == b"legacy-compatible patch"


def test_corrupt_newer_checkpoint_blocks_fallback_to_older_valid_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    import json

    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    older = checkpoint.paths("older", 1)
    older["patch"].write_bytes(b"older patch")
    older_metadata = {
        "schema_version": 3,
        "run_id": "older",
        "task": 1,
        "base_sha": "a" * 40,
        "updated_at": 1.0,
        "patch_bytes": len(b"older patch"),
    }
    older["metadata"].write_text(json.dumps(older_metadata), encoding="utf-8")

    newer = checkpoint.paths("newer", 1)
    newer["patch"].write_bytes(b"newer patch")
    newer["metadata"].write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unreadable; refusing stale-checkpoint fallback"):
        checkpoint._latest_checkpoint()


def test_checkpoint_store_lock_is_released_after_abrupt_owner_exit(tmp_path: Path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    code = (
        "import os; from pathlib import Path; "
        "from scripts.autonomous_engineering_checkpoint_lock import checkpoint_store_lock; "
        f"lock=checkpoint_store_lock(Path({str(tmp_path)!r})); "
        "lock.__enter__(); os._exit(31)"
    )
    child = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert child.returncode == 31

    with checkpoint.checkpoint_store_lock(tmp_path, timeout_seconds=2):
        pass


def test_read_metadata_rejects_unsupported_schema_before_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    _, paths = _state(tmp_path)
    monkeypatch.setattr(checkpoint, "paths", lambda run_id, task: paths)
    value = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    value["schema_version"] = 99
    paths["metadata"].write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(RuntimeError, match="supported v2-v4 ownership contract"):
        checkpoint.read_metadata("run-9", 2)

    assert paths["patch"].read_bytes() == b"preserve this patch"


def test_snapshot_uses_temporary_git_objects_when_checkout_is_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    import subprocess

    repository = tmp_path / "repository"
    repository.mkdir()
    state_directory = tmp_path / "checkpoint-state"
    state_directory.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Checkpoint Test"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "checkpoint@example.invalid"],
        check=True, capture_output=True,
    )
    for folder in ("app", "tests", "docs", "scripts"):
        (repository / folder).mkdir()
    (repository / "app" / "tracked.py").write_text("BASE = True\\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", "base"],
        check=True, capture_output=True,
    )
    base_sha = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    (repository / "app" / "new.py").write_text("NEW = True\\n", encoding="utf-8")
    source_objects = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--git-path", "objects"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    source_objects_path = Path(source_objects)
    if not source_objects_path.is_absolute():
        source_objects_path = repository / source_objects_path
    source_objects_path = source_objects_path.resolve()
    before_objects = subprocess.run(
        ["git", "-C", str(repository), "count-objects", "-v"],
        check=True, capture_output=True, text=True,
    ).stdout

    monkeypatch.setattr(checkpoint, "ROOT", repository)
    observed_environments = []
    original_git = checkpoint.git

    def recording_git(*args, env=None, **kwargs):
        if args and args[0] in {"read-tree", "add", "diff"}:
            observed_environments.append(dict(env or {}))
        return original_git(*args, env=env, **kwargs)

    monkeypatch.setattr(checkpoint, "git", recording_git)
    state = {
        "directory": state_directory,
        "patch": state_directory / "checkpoint.patch",
        "metadata": state_directory / "checkpoint.json",
        "plan": state_directory / "plan.json",
        "stop": state_directory / "checkpoint.stop",
    }

    checkpoint._write_snapshot_unlocked(base_sha, "readonly-test", 1, state=state)

    assert len(observed_environments) == 3
    for environment in observed_environments:
        object_directory = Path(environment["GIT_OBJECT_DIRECTORY"])
        assert object_directory.parent.parent == state_directory
        assert environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] == str(source_objects_path)
        assert Path(environment["GIT_INDEX_FILE"]).parent == object_directory.parent
    assert state["patch"].is_file()
    assert b"app/new.py" in state["patch"].read_bytes()
    after_objects = subprocess.run(
        ["git", "-C", str(repository), "count-objects", "-v"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert after_objects == before_objects
    assert not list(state_directory.glob("checkpoint-git-*"))




def _write_indexed_checkpoint(tmp_path: Path, run_id: str, updated_at: float) -> dict[str, Path]:
    state = checkpoint.paths(run_id, 1)
    patch = f"checkpoint patch for {run_id}".encode("utf-8")
    state["patch"].write_bytes(patch)
    metadata = {
        "schema_version": 4,
        "run_id": run_id,
        "task": 1,
        "base_sha": "d" * 40,
        "watcher_pid": None,
        "watcher_started_at": None,
        "watcher_ready": False,
        "updated_at": updated_at,
        "patch_bytes": len(patch),
    }
    state["metadata"].write_text(json.dumps(metadata), encoding="utf-8")
    return state


def test_checkpoint_index_is_authoritative_and_inactive_pointer_preserves_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    old_state = _write_indexed_checkpoint(tmp_path, "run-old", 500.0)
    active_state = _write_indexed_checkpoint(tmp_path, "run-active", 100.0)

    active_metadata = json.loads(active_state["metadata"].read_text(encoding="utf-8"))
    checkpoint._write_checkpoint_index_unlocked(active_metadata, active_state)

    latest = checkpoint._latest_checkpoint()
    assert latest is not None
    assert latest[0]["run_id"] == "run-active"
    assert latest[1]["patch"].read_bytes() == active_state["patch"].read_bytes()

    index_path = tmp_path / checkpoint.CHECKPOINT_INDEX_NAME
    active_index = json.loads(index_path.read_text(encoding="utf-8"))
    assert active_index["active"] is True
    assert "path" not in active_index
    assert "patch" not in active_index

    checkpoint._deactivate_checkpoint_index_unlocked(tmp_path, "run-active", 1)
    inactive_index = json.loads(index_path.read_text(encoding="utf-8"))
    assert inactive_index["active"] is False
    assert checkpoint._latest_checkpoint() is None

    # Neither the completed snapshot nor older history is deleted/reactivated.
    assert active_state["patch"].is_file()
    assert active_state["metadata"].is_file()
    assert old_state["patch"].is_file()
    assert old_state["metadata"].is_file()


def test_checkpoint_index_corruption_fails_closed_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    _write_indexed_checkpoint(tmp_path, "run-safe", 100.0)
    (tmp_path / checkpoint.CHECKPOINT_INDEX_NAME).write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Checkpoint index is unreadable"):
        checkpoint._latest_checkpoint()


def test_checkpoint_index_patch_mismatch_preserves_snapshot_and_blocks_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(checkpoint, "store_dir", lambda: tmp_path)
    state = _write_indexed_checkpoint(tmp_path, "run-mismatch", 100.0)
    metadata = json.loads(state["metadata"].read_text(encoding="utf-8"))
    checkpoint._write_checkpoint_index_unlocked(metadata, state)
    state["patch"].write_bytes(b"tampered patch with a different length")

    with pytest.raises(RuntimeError, match="patch size"):
        checkpoint._latest_checkpoint()

    assert state["metadata"].is_file()
    assert state["patch"].read_bytes() == b"tampered patch with a different length"
