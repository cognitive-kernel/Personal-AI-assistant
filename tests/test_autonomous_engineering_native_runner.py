from types import SimpleNamespace

import pytest

from app import autonomous_engineering_native_runner as native_runner


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ([], "not_found"),
        (["verified"], "verified"),
        (["verified", "verified"], "ambiguous"),
        (["verified", "unverifiable"], "unverifiable"),
        (["mismatch"], "mismatch"),
    ],
)
def test_native_runner_projects_local_ownership_without_enabling_workflow(tmp_path, monkeypatch, evidence, expected):
    processes = [SimpleNamespace(info={"name": "Runner.Listener"}, evidence=item) for item in evidence]
    monkeypatch.setattr(native_runner.psutil, "process_iter", lambda attrs: iter(processes))
    monkeypatch.setattr(native_runner, "_inspect_listener", lambda process, root: process.evidence)
    result = native_runner.native_runner_status(lambda: tmp_path)
    assert result["local_ownership"] == expected
    assert result["running"] is (expected == "verified")
    assert set(result) == {"local_ownership", "running", "process_detected"}


def test_native_runner_rechecks_creation_time_after_inspection(tmp_path, monkeypatch):
    process = SimpleNamespace(
        pid=53, create_time=lambda: 100.0, exe=lambda: str(tmp_path / "Runner.Listener"),
        status=lambda: "running", is_running=lambda: True,
        environ=lambda: {
            "AUTONOMOUS_ENGINEERING_STATE_BACKEND": "native",
            "AUTONOMOUS_ENGINEERING_PROGRESS_DIR": str(tmp_path),
        },
    )
    monkeypatch.setattr(native_runner, "_same_process_user", lambda process: True)
    monkeypatch.setattr(native_runner.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 200.0))
    assert native_runner._inspect_listener(process, tmp_path) == "unverifiable"
