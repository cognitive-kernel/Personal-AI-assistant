import json
import logging

from app import learning_log


def test_configure_creates_log_file_and_reports_path(tmp_path, monkeypatch):
    path = tmp_path / "diagnostics" / "learning.log"
    monkeypatch.setenv("LEARNING_LOG_PATH", str(path))

    info = learning_log.diagnostics()

    assert info["exists"] is True
    assert info["path"] == str(path.resolve())
    assert path.is_file()


def test_log_event_writes_structured_error_and_redacts_secrets(tmp_path, monkeypatch):
    path = tmp_path / "learning.log"
    monkeypatch.setenv("LEARNING_LOG_PATH", str(path))
    learning_log.configure_learning_logger()

    learning_log.log_event(
        "learning.test_failure",
        level=logging.ERROR,
        job_id=42,
        api_token="should-not-appear",
        detail="test assertion failed",
    )

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    record = records[-1]
    assert record["event"] == "learning.test_failure"
    assert record["context"]["job_id"] == 42
    assert record["context"]["api_token"] == "[REDACTED]"
    assert "should-not-appear" not in path.read_text(encoding="utf-8")
