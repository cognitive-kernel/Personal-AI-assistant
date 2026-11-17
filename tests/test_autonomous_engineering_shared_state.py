from __future__ import annotations

import json
from pathlib import Path

import platformdirs
import pytest

from app import autonomous_engineering_state as state


def _configure_posix_state(monkeypatch, home: Path, site_root: Path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(platformdirs, "site_data_path", lambda *args, **kwargs: site_root)
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)


def test_posix_default_state_uses_system_shared_root_and_migrates_legacy_state(
    monkeypatch, tmp_path: Path,
):
    home = tmp_path / "legacy-home"
    site_root = tmp_path / "system-data"
    legacy = home / ".personal-ai-assistant" / "autonomous-engineering"
    legacy.mkdir(parents=True)
    (legacy / "pending-manual-task.txt").write_text("preserve this task", encoding="utf-8")
    _configure_posix_state(monkeypatch, home, site_root)

    resolved = state.execution_state_directory(
        is_windows_fn=lambda: False,
        machine_state_fn=lambda: "",
    )

    assert resolved == site_root / "autonomous-engineering"
    assert (resolved / "pending-manual-task.txt").read_text(encoding="utf-8") == "preserve this task"
    assert (legacy / "pending-manual-task.txt").read_text(encoding="utf-8") == "preserve this task"
    markers = list(resolved.glob(".legacy-migration-*.json"))
    assert len(markers) == 1
    marker = json.loads(markers[0].read_text(encoding="utf-8"))
    assert marker["target"] == str(resolved)
    assert state.execution_state_directory(
        is_windows_fn=lambda: False,
        machine_state_fn=lambda: "",
    ) == resolved


def test_posix_shared_state_migration_refuses_conflicts_without_overwriting_either_copy(
    monkeypatch, tmp_path: Path,
):
    home = tmp_path / "legacy-home"
    site_root = tmp_path / "system-data"
    legacy = home / ".personal-ai-assistant" / "autonomous-engineering"
    shared = site_root / "autonomous-engineering"
    legacy.mkdir(parents=True)
    shared.mkdir(parents=True)
    (legacy / "pending-manual-task.txt").write_text("legacy task", encoding="utf-8")
    (shared / "pending-manual-task.txt").write_text("different shared task", encoding="utf-8")
    _configure_posix_state(monkeypatch, home, site_root)

    with pytest.raises(RuntimeError, match="conflicting files"):
        state.execution_state_directory(
            is_windows_fn=lambda: False,
            machine_state_fn=lambda: "",
        )

    assert (legacy / "pending-manual-task.txt").read_text(encoding="utf-8") == "legacy task"
    assert (shared / "pending-manual-task.txt").read_text(encoding="utf-8") == "different shared task"


def test_posix_programdata_override_is_shared_and_migrates_legacy_state(monkeypatch, tmp_path: Path):
    home = tmp_path / "legacy-home"
    program_data = tmp_path / "shared-program-data"
    legacy = home / ".personal-ai-assistant" / "autonomous-engineering"
    legacy.mkdir(parents=True)
    (legacy / "checkpoint.patch").write_bytes(b"checkpoint")
    _configure_posix_state(monkeypatch, home, tmp_path / "unused-site-data")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))

    resolved = state.execution_state_directory(
        is_windows_fn=lambda: False,
        machine_state_fn=lambda: "",
    )

    assert resolved == program_data / "PersonalAIAssistant" / "autonomous-engineering"
    assert (resolved / "checkpoint.patch").read_bytes() == b"checkpoint"


def test_posix_programdata_override_rejects_per_user_home(monkeypatch, tmp_path: Path):
    home = tmp_path / "legacy-home"
    _configure_posix_state(monkeypatch, home, tmp_path / "system-data")
    monkeypatch.setenv("PROGRAMDATA", str(home))

    with pytest.raises(RuntimeError, match="inside a per-user home"):
        state.execution_state_directory(
            is_windows_fn=lambda: False,
            machine_state_fn=lambda: "",
        )


def test_posix_shared_state_resolution_works_without_a_home_directory(monkeypatch, tmp_path: Path):
    site_root = tmp_path / "system-data"
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("home is unavailable"))),
    )
    monkeypatch.setattr(platformdirs, "site_data_path", lambda *args, **kwargs: site_root)
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)

    resolved = state.execution_state_directory(
        is_windows_fn=lambda: False,
        machine_state_fn=lambda: "",
    )

    assert resolved == site_root / "autonomous-engineering"


def test_posix_rejects_configured_state_inside_current_users_home(monkeypatch, tmp_path: Path):
    home = tmp_path / "legacy-home"
    _configure_posix_state(monkeypatch, home, tmp_path / "system-data")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", str(home / "private-state"))

    with pytest.raises(ValueError, match="per-user home directory"):
        state.execution_state_directory(
            is_windows_fn=lambda: False,
            machine_state_fn=lambda: "",
        )


def test_posix_rejects_platform_site_data_path_that_resolves_inside_user_home(
    monkeypatch, tmp_path: Path,
):
    home = tmp_path / "legacy-home"
    _configure_posix_state(monkeypatch, home, home / ".local" / "share")

    with pytest.raises(RuntimeError, match="inside the current user's home"):
        state.execution_state_directory(
            is_windows_fn=lambda: False,
            machine_state_fn=lambda: "",
        )


def test_posix_migration_does_not_replace_shared_lock_files(monkeypatch, tmp_path: Path):
    home = tmp_path / "legacy-home"
    site_root = tmp_path / "system-data"
    legacy = home / ".personal-ai-assistant" / "autonomous-engineering"
    legacy.mkdir(parents=True)
    (legacy / "repository-execution.lock").write_bytes(b"legacy lock metadata")
    (legacy / "autonomous-durable-state.lock").write_bytes(b"legacy durable lock")
    (legacy / "state.json").write_text('{"ok":true}', encoding="utf-8")
    _configure_posix_state(monkeypatch, home, site_root)

    resolved = state.execution_state_directory(
        is_windows_fn=lambda: False,
        machine_state_fn=lambda: "",
    )

    assert (resolved / "state.json").read_text(encoding="utf-8") == '{"ok":true}'
    assert (resolved / "repository-execution.lock").read_bytes() != b"legacy lock metadata"
    assert (resolved / "autonomous-durable-state.lock").read_bytes() != b"legacy durable lock"
