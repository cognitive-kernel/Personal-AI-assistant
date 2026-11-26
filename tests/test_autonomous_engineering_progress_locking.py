from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from scripts import autonomous_engineering_progress as progress


def test_progress_persist_uses_shared_durable_state_lock(monkeypatch, tmp_path: Path):
    calls: list[tuple[Path, float]] = []

    @contextmanager
    def fake_lock(directory: Path, *, timeout_seconds: float):
        calls.append((Path(directory), timeout_seconds))
        yield

    monkeypatch.setattr(progress, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(progress, "durable_state_lock", fake_lock)

    assert progress.persist({
        "run_id": "progress-lock-test",
        "run_attempt": "1",
        "status": "running",
        "phase": "test",
    }) is True

    assert calls == [(tmp_path, 5)]
    assert (tmp_path / "current.json").is_file()
    assert (tmp_path / "events.jsonl").is_file()
