from pathlib import Path


def test_recovery_workflow_has_a_finite_failure_streak_circuit_breaker():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-recovery.yml"
    ).read_text(encoding="utf-8")
    guard = "          max_consecutive_failures = 3"
    breaker = "          if streak >= max_consecutive_failures:"
    dispatch = '              "/actions/workflows/autonomous-codex-engineering.yml/dispatches",'

    assert guard in workflow
    assert breaker in workflow
    assert "CIRCUIT BREAKER OPEN" in workflow
    assert "durable task/checkpoint is preserved" in workflow
    assert workflow.index(breaker) < workflow.index(dispatch)
    assert 'if [[ "${PAUSED,,}" == "true" ]]' in workflow


def test_recovery_workflow_keeps_backoff_bounded_before_circuit_breaker():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-recovery.yml"
    ).read_text(encoding="utf-8")

    assert "delay_seconds = min(240, 30 * (2 ** min(streak - 1, 3)))" in workflow
    assert "time.sleep(delay_seconds)" in workflow
