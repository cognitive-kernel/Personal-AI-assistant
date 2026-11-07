from scripts.autonomous_engineering_pending_task import validate_manual_task_submission


REPOSITORY = "cognitive-kernel/Personal-AI-assistant"


def _open_pr():
    return {
        "state": "open",
        "user": {"login": "github-actions[bot]"},
        "head": {
            "ref": "codex/local-engineering-123-1-task-1",
            "repo": {"full_name": REPOSITORY},
        },
        "base": {"ref": "main"},
    }


def test_manual_task_is_rejected_while_previous_autonomous_pr_is_open():
    try:
        validate_manual_task_submission(
            manual_task="new task",
            pending_task="",
            open_pull_requests=[_open_pr()],
            repository=REPOSITORY,
        )
    except ValueError as exc:
        assert "previous autonomous task PR is still open" in str(exc)
    else:
        raise AssertionError("A new manual task must not supersede an unaccepted PR.")


def test_different_manual_task_cannot_replace_existing_pending_task():
    try:
        validate_manual_task_submission(
            manual_task="new task",
            pending_task="original task",
            open_pull_requests=[],
            repository=REPOSITORY,
        )
    except ValueError as exc:
        assert "cannot be replaced" in str(exc)
    else:
        raise AssertionError("Pending task replacement must fail closed.")


def test_same_manual_task_can_resume_after_interrupted_run_without_open_pr():
    validate_manual_task_submission(
        manual_task="original task",
        pending_task="original task",
        open_pull_requests=[],
        repository=REPOSITORY,
    )


def test_unrelated_pull_request_does_not_block_manual_task():
    unrelated = _open_pr()
    unrelated["user"]["login"] = "contributor"
    validate_manual_task_submission(
        manual_task="new task",
        pending_task="",
        open_pull_requests=[unrelated],
        repository=REPOSITORY,
    )


def test_empty_recovery_dispatch_preserves_pending_task():
    validate_manual_task_submission(
        manual_task="",
        pending_task="original task",
        open_pull_requests=[_open_pr()],
        repository=REPOSITORY,
    )


def test_manual_task_is_persisted_before_codex_and_ollama_preflight():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    capture = workflow.index("Capture live GitHub state with the read-only workflow token")
    codex_install = workflow.index("Ensure Codex CLI is installed for the Runner service account")
    ollama_check = workflow.index("Verify local Ollama and Codex prerequisites")
    sandbox_smoke = workflow.index("Verify Ollama and a real workspace-confined Codex tool call")

    assert capture < codex_install < ollama_check < sandbox_smoke


def test_manual_task_persistence_precedes_schedule_gate_and_runner_prerequisites():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    persist_start = workflow.index("  persist-manual-task:")
    gate_start = workflow.index("  schedule-gate:")
    codex_start = workflow.index("  codex:")
    persist_job = workflow[persist_start:gate_start]
    gate_job = workflow[gate_start:codex_start]
    codex_job = workflow[codex_start:]

    assert persist_start < gate_start < codex_start
    assert "if: github.event_name == 'workflow_dispatch' && inputs.manual_task != '' && github.ref == 'refs/heads/main'" in persist_job
    assert "runs-on: [self-hosted, Windows, X64, ollama]" in persist_job
    assert "ref: main" in persist_job
    assert "persist-credentials: false" in persist_job
    assert "persist_autonomous_manual_task.ps1" in persist_job
    assert "needs: persist-manual-task" in gate_job
    assert "if: always()" in gate_job
    assert "needs: [persist-manual-task, schedule-gate]" in codex_job
    assert "if: always()" in codex_job
    assert "concurrency:" in codex_job
    assert "group: autonomous-local-engineering-${{ github.repository }}" in codex_job
    assert "Persist manual task before any runner prerequisite checks" not in codex_job
    workflow_header = workflow.split("jobs:", 1)[0]
    assert "concurrency:" not in workflow_header
def test_early_manual_task_persistence_script_is_protected_and_validated_in_ci():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "persist_autonomous_manual_task.ps1").read_text(encoding="utf-8")
    controller = (root / "scripts" / "autonomous_engineering_cycle.py").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")

    assert "scripts/persist_autonomous_manual_task.ps1" in controller
    assert "Invoke-RestMethod" in script
    assert "Move-Item -LiteralPath $temporaryPath -Destination $pendingPath -Force" in script
    assert "persist_autonomous_manual_task.ps1" in workflow
