from app.autonomous_engineering_run_logs import build_run_logs_payload


def test_run_log_payload_extracts_failed_jobs_and_uses_latest_diagnostic():
    sanitized = []
    result = build_run_logs_payload(
        run={"id": 42, "status": "completed", "conclusion": "failure", "url": "https://example.test/run/42"},
        jobs=[
            {"name": "build", "status": "completed", "conclusion": "success", "steps": []},
            {"name": "codex", "status": "completed", "conclusion": "failure", "steps": [
                {"name": "Execute", "conclusion": "failure"},
                {"name": "Upload", "conclusion": "skipped"},
            ]},
        ],
        raw_log="raw workflow output",
        diagnostic={"diagnostics": [
            {"summary": "older", "stage": "setup"},
            {"summary": "latest", "stage": "codex"},
        ]},
        max_log_chars=100,
        sanitize_log=lambda value: sanitized.append(value) or "safe output",
        failure_summary=lambda *_: "fallback",
    )

    assert result["run"]["id"] == 42
    assert result["failures"] == [{
        "name": "codex",
        "status": "completed",
        "conclusion": "failure",
        "failed_steps": [{"name": "Execute", "conclusion": "failure"}],
    }]
    assert result["error_summary"] == "latest"
    assert result["failure_stage"] == "codex"
    assert result["diagnostics_available"] is True
    assert result["logs"] == "safe output"
    assert sanitized == ["raw workflow output"]


def test_run_log_payload_uses_failure_summary_and_fallback_for_empty_log():
    result = build_run_logs_payload(
        run={"id": 7, "status": "completed"},
        jobs=[{"name": "test", "status": "completed", "conclusion": "failure"}],
        raw_log="",
        diagnostic=None,
        max_log_chars=10,
        sanitize_log=lambda value: value,
        failure_summary=lambda value, failures: f"{value}:{len(failures)}",
    )

    assert result["error_summary"] == "GitHub گزارشی برای این اجرا برنگرداند؛ جزئیات مرحله‌های ناموفق در بالا آمده است.:1"
    assert result["failure_stage"] == "test"
    assert result["truncated"] is True


def test_run_log_payload_reports_incomplete_active_run_without_diagnostics():
    result = build_run_logs_payload(
        run={"id": 8, "status": "in_progress"},
        jobs=[],
        raw_log="",
        diagnostic=None,
        max_log_chars=500,
        sanitize_log=lambda value: value,
        failure_summary=lambda *_: None,
    )

    assert result["logs"] == "گزارش کامل تا پایان اجرای Workflow در دسترس نیست."
    assert result["diagnostics_available"] is False
