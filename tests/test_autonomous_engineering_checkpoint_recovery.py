from __future__ import annotations

from pathlib import Path

import pytest

from scripts import autonomous_engineering_checkpoint as checkpoint


def _state(tmp_path: Path) -> tuple[dict[str, object], dict[str, Path]]:
    paths = {
        "directory": tmp_path,
        "patch": tmp_path / "checkpoint.patch",
        "metadata": tmp_path / "checkpoint.json",
        "plan": tmp_path / "plan.json",
        "stop": tmp_path / "checkpoint.stop",
    }
    paths["patch"].write_bytes(b"preserve this patch")
    paths["metadata"].write_text('{"run_id":"run-9","task":2}', encoding="utf-8")
    metadata = {
        "schema_version": 4,
        "run_id": "run-9",
        "task": 2,
        "base_sha": "a" * 40,
        "watcher_pid": 4321,
        "watcher_started_at": 1234.5,
        "watcher_ready": True,
    }
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
    monkeypatch.setattr(checkpoint, "read_metadata", lambda run_id, task: metadata)

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
    monkeypatch.setattr(checkpoint, "watcher_marker_paths", lambda run_id, task: (ready, error))
    monkeypatch.setattr(checkpoint, "read_metadata", lambda run_id, task: metadata)

    def refuse_stop(_metadata):
        raise RuntimeError("previous watcher is not verifiably stopped")

    monkeypatch.setattr(checkpoint, "stop_existing_watcher", refuse_stop)

    with pytest.raises(RuntimeError, match="not verifiably stopped"):
        checkpoint.watch("a" * 40, "run-9", 2, 5)

    assert paths["patch"].read_bytes() == b"preserve this patch"
    assert ready.read_text(encoding="utf-8") == "old readiness"
    assert error.read_text(encoding="utf-8") == "old error"
