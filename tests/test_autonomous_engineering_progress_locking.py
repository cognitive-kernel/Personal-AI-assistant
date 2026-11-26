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
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_EXECUTION_OWNER_TOKEN", "a" * 64)

    assert progress.persist({
        "run_id": "progress-lock-test",
        "run_attempt": "1",
        "status": "running",
        "phase": "test",
    }) is True

    assert calls == [(tmp_path, 5)]
    assert (tmp_path / "current.json").is_file()
    assert (tmp_path / "events.jsonl").is_file()
    import json
    current = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    event = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert current["execution_owner_token"] == "a" * 64
    assert event["execution_owner_token"] == "a" * 64

def test_dashboard_progress_projection_never_exposes_execution_owner_token(monkeypatch):
    from app import autonomous_engineering_progress as dashboard_progress

    owner_token = "a" * 64
    monkeypatch.setattr(
        dashboard_progress,
        "_shared_state_snapshot",
        lambda: {
            "current": {
                "run_id": "progress-lock-test",
                "run_attempt": "1",
                "status": "running",
                "execution_owner_token": owner_token,
            },
            "history": [],
            "baseline": None,
            "events": [{
                "type": "progress_event",
                "execution_owner_token": owner_token,
            }],
            "storage_configured": True,
        },
    )

    result = dashboard_progress.get_progress()

    assert "execution_owner_token" not in result["current"]
    assert all("execution_owner_token" not in event for event in result["events"])
    assert owner_token not in repr(result)
