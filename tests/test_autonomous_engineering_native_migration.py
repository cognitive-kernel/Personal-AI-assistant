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
