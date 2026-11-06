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
