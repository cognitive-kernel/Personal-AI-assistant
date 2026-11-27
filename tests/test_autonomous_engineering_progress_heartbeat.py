from __future__ import annotations

from argparse import Namespace

from scripts import autonomous_engineering_progress as progress


def test_codex_heartbeat_reports_waiting_for_first_ollama_event(monkeypatch):
    monkeypatch.setattr(progress, "plan_state", lambda: (0, 0, None))

    record = progress.codex_heartbeat_record(
        Namespace(run_id="44305904911", run_attempt="1"),
        elapsed_seconds=125,
        event_count=0,
    )

    assert record["run_id"] == "44305904911"
    assert record["run_attempt"] == "1"
    assert record["status"] == "running"
    assert record["phase"] == "coding"
    assert "اولین رویداد" in record["activity"]
    assert "2 دقیقه" in record["activity"]
    assert record["plan_completed"] == 0
    assert record["plan_total"] == 0


def test_codex_heartbeat_reports_received_events_and_preserves_plan(monkeypatch):
    monkeypatch.setattr(progress, "plan_state", lambda: (2, 5, None))
    monkeypatch.setattr(progress, "plan_percentage", lambda completed, total: 40.0)

    record = progress.codex_heartbeat_record(
        Namespace(run_id="44305904911", run_attempt="1"),
        elapsed_seconds=245,
        event_count=7,
    )

    assert "7 رویداد" in record["activity"]
    assert "4 دقیقه" in record["activity"]
    assert record["plan_completed"] == 2
    assert record["plan_total"] == 5
    assert record["plan_percent"] == 40.0


def test_codex_heartbeat_prefers_current_task_title(monkeypatch):
    monkeypatch.setattr(progress, "plan_state", lambda: (1, 3, "افزودن تست رگرسیون"))

    record = progress.codex_heartbeat_record(
        Namespace(run_id="44305904911", run_attempt="1"),
        elapsed_seconds=60,
        event_count=0,
    )

    assert record["activity"] == "افزودن تست رگرسیون"


def test_codex_heartbeat_console_line_is_ascii_safe_for_windows_code_pages():
    line = progress.codex_heartbeat_console_line(elapsed_seconds=125, event_count=0)

    assert line.isascii()
    assert "elapsed_minutes=2" in line
    assert "jsonl_events=0" in line
    assert "state=waiting_for_first_event" in line


def test_codex_heartbeat_console_line_reports_received_events():
    line = progress.codex_heartbeat_console_line(elapsed_seconds=245, event_count=7)

    assert line.isascii()
    assert "elapsed_minutes=4" in line
    assert "jsonl_events=7" in line
    assert "state=receiving_events" in line




def test_final_agent_message_report_preserves_markerless_text_for_typed_validation(tmp_path):
    report = tmp_path / "report.md"
    assert progress.recover_final_agent_message_report(
        report,
        "The implementation is complete but the marker was omitted.",
        turn_completed=True,
        turn_failed=False,
    )
    assert report.read_text(encoding="utf-8") == "The implementation is complete but the marker was omitted.\n"
    assert progress._has_terminal_completion_marker(report.read_text(encoding="utf-8")) is False


def test_final_agent_message_recovery_accepts_only_a_plain_terminal_marker(tmp_path):
    report = tmp_path / "report.md"
    assert progress.recover_final_agent_message_report(
        report,
        "Evidence\n- quoted marker: TASK_COMPLETE\nTASK_COMPLETE",
        turn_completed=True,
        turn_failed=False,
    )
    assert progress._has_terminal_completion_marker(report.read_text(encoding="utf-8")) is True
    assert not progress.recover_final_agent_message_report(
        report,
        "A later answer without a marker",
        turn_completed=True,
        turn_failed=False,
    )


def test_final_agent_message_recovery_does_not_fabricate_missing_turn_messages(tmp_path):
    report = tmp_path / "report.md"
    assert not progress.recover_final_agent_message_report(
        report,
        None,
        turn_completed=True,
        turn_failed=False,
    )
    assert not report.exists()



def test_progress_records_include_workflow_correlation_and_job(monkeypatch):
    correlation_id = "b" * 32
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CORRELATION_ID", correlation_id)
    monkeypatch.setenv("GITHUB_JOB", "codex")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_JOB_ID", "987654321")
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_STEP_ID", "autonomous-engineering-cycle")

    record = progress._with_run_correlation({"run_id": "12345", "phase": "coding"})

    assert record["correlation_id"] == correlation_id
    assert record["job"] == "codex"
    assert record["job_id"] == 987654321
    assert record["step_id"] == "autonomous-engineering-cycle"
    assert record["run_id"] == "12345"
