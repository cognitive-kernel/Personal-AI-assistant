from pathlib import Path


def test_recovery_workflow_checks_out_trusted_policy_and_invokes_it():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-recovery.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "jobs:\n  recover:" in workflow
    assert "    steps:\n      - name: Check out trusted main recovery policy" in workflow
    assert "actions: write" in workflow
    assert "contents: read" in workflow
    assert "actions/checkout@" in workflow
    assert "ref: main" in workflow
    assert "persist-credentials: false" in workflow
    assert "python scripts/autonomous_engineering_recovery.py" in workflow
    assert "FAILED_RUN_ID:" in workflow
    policy = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "autonomous_engineering_recovery.py"
    ).read_text(encoding="utf-8")
    assert "def _live_execution_mode(client: GitHubActionsClient)" in policy
    assert "def _live_pause_enabled(client: GitHubActionsClient)" in policy
    assert '"/actions/variables/AUTONOMOUS_ENGINEERING_MODE"' in policy
    assert '"/actions/variables/AUTONOMOUS_ENGINEERING_PAUSED"' in policy
    assert "Recovery fails closed" in policy
    assert "AUTONOMOUS_ENGINEERING_MODE:" not in workflow
    assert "AUTONOMOUS_ENGINEERING_PAUSED:" not in workflow


def test_recovery_workflow_does_not_hide_policy_or_dispatch_logic_inline():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github" / "workflows" / "autonomous-codex-recovery.yml"
    ).read_text(encoding="utf-8")

    assert "CIRCUIT BREAKER OPEN" not in workflow
    assert "max_consecutive_failures" not in workflow
    assert "/dispatches" not in workflow


def test_recovery_policy_uses_bounded_backoff_and_a_finite_breaker():
    policy = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "autonomous_engineering_recovery.py"
    ).read_text(encoding="utf-8")

    assert "MAX_CONSECUTIVE_FAILURES = 3" in policy
    assert "delay_seconds = min(240, 30 * (2 ** min(max(streak - 1, 0), 3)))" in policy
    assert "time.sleep(delay_seconds)" in policy
    assert "CIRCUIT BREAKER OPEN" in policy
    assert "durable task/checkpoint is preserved" in policy
