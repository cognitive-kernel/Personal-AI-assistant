from __future__ import annotations

import json
import pytest

from app.autonomous_engineering_native_migration import stage_native_migration
from app.autonomous_engineering_state import exclusive_file_lock


def test_migration_stages_verified_copy_without_switching_authority(tmp_path):
    source, target = tmp_path / "legacy", tmp_path / "native"
    source.mkdir()
    (source / "current.json").write_text('{"status":"completed"}')
    result = stage_native_migration(source, target, require_idle=lambda: None)
    assert result["staged"] and not result["activated"]
    assert (source / "current.json").read_bytes() == (target / "current.json").read_bytes()
    assert json.loads((target / ".native-migration.json").read_text())["activation_permitted"] is False
    assert stage_native_migration(source, target, require_idle=lambda: None)["staged"]


def test_migration_refuses_owned_source(tmp_path):
    source, target = tmp_path / "legacy", tmp_path / "native"
    source.mkdir()
    with exclusive_file_lock(source / "repository-execution.lock", timeout_seconds=0, timeout_message="busy"):
        with pytest.raises(TimeoutError):
            stage_native_migration(source, target, require_idle=lambda: None)
    assert not target.exists()


def test_migration_refuses_conflicting_destination(tmp_path):
    source, target = tmp_path / "legacy", tmp_path / "native"
    source.mkdir()
    target.mkdir()
    (source / "current.json").write_text('{"status":"completed"}')
    (target / "current.json").write_text('{"status":"running"}')
    with pytest.raises((RuntimeError, OSError)):
        stage_native_migration(source, target, require_idle=lambda: None)
    assert json.loads((target / "current.json").read_text())["status"] == "running"


def test_migration_refuses_remote_activity_before_copy(tmp_path):
    source, target = tmp_path / "legacy", tmp_path / "native"
    source.mkdir()
    def active():
        raise RuntimeError("remote active")
    with pytest.raises(RuntimeError, match="remote active"):
        stage_native_migration(source, target, require_idle=active)
    assert not target.exists()


def test_legacy_lock_placeholder_can_be_staged(tmp_path):
    source, target = tmp_path / "legacy", tmp_path / "native"
    source.mkdir()
    (source / "repository-execution.lock").write_bytes(bytes((92, 48)))
    assert stage_native_migration(source, target, require_idle=lambda: None)["staged"]
    assert not (target / "repository-execution.lock").exists()


def test_staged_snapshot_cannot_become_live_by_source_disappearance(tmp_path, monkeypatch):
    from app.autonomous_engineering_native_config import resolve_native_state_directory
    from platformdirs import user_data_path

    target = tmp_path / "native"
    target.mkdir()
    (target / ".native-migration.json").write_text("{}")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(target))
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_REQUIRE_MACHINE_STATE_DIR", raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "no-machine-state"))
    monkeypatch.setattr("platformdirs.user_data_path", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "empty-home")
    # Existing machine path is permitted only if it is not a staged snapshot.
    with pytest.raises(RuntimeError, match="Staged migration"):
        resolve_native_state_directory(is_windows=True, machine_state=lambda: str(target))
