from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
import os
import re
import subprocess
import sys
import sysconfig
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .autonomous_engineering_github import (
        api as _github_api,
        dispatch_and_wait_all as _github_dispatch_and_wait_all,
        git_auth_env as _github_git_auth_env,
        graphql as _github_graphql,
    )
    from .autonomous_engineering_pull_requests import (
        find_open_autonomous_pr as _find_open_autonomous_pr,
        complete_pending_pr as _complete_pending_pr,
        complete_merged_pr as _complete_merged_pr,
        publish_pull_request as _publish_pull_request,
    )
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_github import (
        api as _github_api,
        dispatch_and_wait_all as _github_dispatch_and_wait_all,
        git_auth_env as _github_git_auth_env,
        graphql as _github_graphql,
    )
    from autonomous_engineering_pull_requests import (
        find_open_autonomous_pr as _find_open_autonomous_pr,
        complete_pending_pr as _complete_pending_pr,
        complete_merged_pr as _complete_merged_pr,
        publish_pull_request as _publish_pull_request,
    )


try:
    from .autonomous_engineering_checkpoint_contract import metadata_confirms_watcher_ready
    from .autonomous_engineering_checkpoint_process import process_start_time
    from .autonomous_engineering_execution_lock import repository_execution_lock
    from .autonomous_engineering_state_lock import durable_state_lock
    from .autonomous_engineering_state_paths import execution_state_directory
    from .autonomous_engineering_pending_pr_state import (
        autonomous_state_directory as _autonomous_state_dir,
        pending_pr_path as _pending_pr_path,
        save_pending_pr_unlocked as _save_pending_pr_unlocked,
        load_pending_pr_unlocked as _load_pending_pr_unlocked,
        save_pending_pr,
        load_pending_pr,
        clear_pending_pr,
        clear_pending_task,
        clear_pending_state,
        recover_pending_state_cleanup_unlocked,
    )
    from .autonomous_engineering_pending_pr_recovery import reconcile_pending_pr_state_unlocked
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_checkpoint_contract import metadata_confirms_watcher_ready
    from autonomous_engineering_checkpoint_process import process_start_time
    from autonomous_engineering_execution_lock import repository_execution_lock
    from autonomous_engineering_state_lock import durable_state_lock
    from autonomous_engineering_state_paths import execution_state_directory
    from autonomous_engineering_pending_pr_state import (
        autonomous_state_directory as _autonomous_state_dir,
        pending_pr_path as _pending_pr_path,
        save_pending_pr_unlocked as _save_pending_pr_unlocked,
        load_pending_pr_unlocked as _load_pending_pr_unlocked,
        save_pending_pr,
        load_pending_pr,
        clear_pending_pr,
        clear_pending_task,
        clear_pending_state,
        recover_pending_state_cleanup_unlocked,
    )
    from autonomous_engineering_pending_pr_recovery import reconcile_pending_pr_state_unlocked

try:
    from . import autonomous_engineering_cycle_checkpoints as _cycle_checkpoints
    from . import autonomous_engineering_cycle_workspace as _cycle_workspace
except ImportError:  # Direct script execution from the scripts directory.
    import autonomous_engineering_cycle_checkpoints as _cycle_checkpoints
    import autonomous_engineering_cycle_workspace as _cycle_workspace

try:
    from .autonomous_engineering_retry_policy import RetryPolicy
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_retry_policy import RetryPolicy

try:
    from .autonomous_engineering_codex_config import CODEX_TASK_TIMEOUT_SECONDS
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_codex_config import CODEX_TASK_TIMEOUT_SECONDS

try:
    from .autonomous_engineering_command_runner import run_command as _run_command
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_command_runner import run_command as _run_command


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_SCRIPT = Path(__file__).resolve().with_name("autonomous_engineering_checkpoint.py")
ALLOWED = ("app/", "tests/", "docs/", "scripts/")
RUNTIME_FILES = {".agent-request.json", ".agent-github-state.json", ".agent-progress.json"}
PROTECTED = {
    ".github/workflows/autonomous-codex-engineering.yml",
    "scripts/autonomous_engineering_cycle.py",
    "scripts/autonomous_engineering_progress.py",
    "scripts/autonomous_engineering_checkpoint.py",
    "scripts/autonomous_engineering_pending_task.py",
    "scripts/persist_autonomous_manual_task.ps1",
}
MARKERS = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:`{1,3}\s*)?(TASK_COMPLETE|QUEUE_EMPTY|TASK_BLOCKED)(?:\s*`{1,3})?[.!]?\s*$")
CYCLE_LIMIT = 6 * 60 * 60
DEFAULT_COMMAND_TIMEOUT_SECONDS = 5 * 60
TEST_SUITE_TIMEOUT_SECONDS = 15 * 60
PULL_REQUEST_VALIDATION_TIMEOUT_SECONDS = 25 * 60
TASK_RETRY_POLICY = RetryPolicy(max_attempts=2, max_elapsed_seconds=90 * 60)
CODEX_RETRY_POLICY = RetryPolicy(max_attempts=2, max_elapsed_seconds=45 * 60)


@contextmanager
def timed_stage(name: str):
    """Emit durable console timing for each expensive controller phase."""
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(name)).strip("_")[:100] or "unknown"
    started = time.monotonic()
    print(f"[autonomous-timing] stage={safe_name} status=started", flush=True)
    try:
        yield
    finally:
        elapsed = max(0.0, time.monotonic() - started)
        print(
            f"[autonomous-timing] stage={safe_name} status=finished elapsed_seconds={elapsed:.1f}",
            flush=True,
        )


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = False,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("Command timeout must be a positive integer.")
    try:
        return _run_command(
            command, cwd=ROOT, env=env, capture=capture,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        executable = Path(command[0]).name if command else "<empty-command>"
        raise RuntimeError(
            f"Autonomous command '{executable}' exceeded its {timeout_seconds}-second deadline."
        ) from exc


def checked(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> str:
    result = run(command, env=env, capture=True, timeout_seconds=timeout_seconds)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {command[0]}\n"
            f"{(result.stderr or result.stdout or '')[-4000:]}"
        )
    return (result.stdout or "").strip()


def record(
    run_id: str, attempt: str, phase: str, label: str, activity: str,
    *, status: str = "running",
) -> None:
    checked([
        sys.executable, "scripts/autonomous_engineering_progress.py", "record",
        "--run-id", run_id, "--run-attempt", attempt,
        "--phase", phase, "--label", label, "--activity", activity,
        "--status", status,
    ])

def checkpoint_command(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return _cycle_checkpoints.checkpoint_command(
        *args, capture=capture, run_command=run, python_executable=sys.executable,
        checkpoint_script=CHECKPOINT_SCRIPT,
    )


def restore_checkpoint(base_sha: str) -> bool:
    return _cycle_checkpoints.restore_checkpoint(
        base_sha, os_name=os.name, programdata=os.getenv("PROGRAMDATA"),
        checkpoint_command=checkpoint_command,
    )


def start_checkpoint_watcher(base_sha: str, run_id: str, task: int) -> subprocess.Popen[Any] | None:
    return _cycle_checkpoints.start_checkpoint_watcher(
        base_sha, run_id, task, os_module=os, subprocess_module=subprocess,
        sys_module=sys, sysconfig_module=sysconfig, time_module=time, root=ROOT,
        checkpoint_script=CHECKPOINT_SCRIPT, execution_state_directory_fn=execution_state_directory,
        process_start_time_fn=process_start_time,
        metadata_confirms_watcher_ready_fn=metadata_confirms_watcher_ready,
        programdata=os.getenv("PROGRAMDATA"),
    )


def stop_checkpoint_watcher(
    watcher: subprocess.Popen[Any] | None, base_sha: str, run_id: str, task: int,
) -> None:
    return _cycle_checkpoints.stop_checkpoint_watcher(
        watcher, base_sha, run_id, task, subprocess_module=subprocess, os_name=os.name,
        programdata=os.getenv("PROGRAMDATA"), checkpoint_command=checkpoint_command,
    )


def clear_checkpoint(run_id: str | None = None, task: int | None = None) -> None:
    return _cycle_checkpoints.clear_checkpoint(
        run_id, task, os_name=os.name, programdata=os.getenv("PROGRAMDATA"),
        checkpoint_command=checkpoint_command,
    )


def snapshot(run_id: str, attempt: str) -> None:
    return _cycle_workspace.snapshot(
        run_id, attempt, checked=checked, python_executable=sys.executable,
    )


def task_marker(report: Path) -> str:
    return _cycle_workspace.task_marker(report, MARKERS)



_MAX_RUN_DIAGNOSTIC_CHARS = 12000


def _sanitize_run_diagnostic_text(value: str) -> str:
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value))
    value = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(authorization\s*:\s*basic\s+)\S+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)
    value = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        "[REDACTED]",
        value,
    )
    value = re.sub(
        r"""(?i)\b[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|gh[_-]?token)[\w-]*(\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s]+)""",
        r"[REDACTED]\1[REDACTED]",
        value,
    )
    if len(value) > _MAX_RUN_DIAGNOSTIC_CHARS:
        value = "[بخش ابتدایی گزارش به‌دلیل محدودیت حجم حذف شد.]\n" + value[-_MAX_RUN_DIAGNOSTIC_CHARS:]
    return value.strip()


def _read_report_diagnostic(report: Path) -> str:
    try:
        if not report.is_file():
            return ""
        size = report.stat().st_size
        with report.open("rb") as stream:
            if size > _MAX_RUN_DIAGNOSTIC_CHARS * 4:
                stream.seek(-_MAX_RUN_DIAGNOSTIC_CHARS * 4, os.SEEK_END)
            raw = stream.read(_MAX_RUN_DIAGNOSTIC_CHARS * 4 + 1)
        value = raw.decode("utf-8-sig", errors="replace")
    except OSError:
        return ""
    return _sanitize_run_diagnostic_text(MARKERS.sub("", value).strip())


def persist_run_diagnostic(
    run_id: str,
    run_attempt: str,
    *,
    task: int | None,
    attempt: int | None,
    stage: str,
    category: str,
    summary: str,
    detail: str = "",
) -> bool:
    """Persist a bounded, redacted explanation for the local dashboard."""
    if not re.fullmatch(r"\d{1,20}", str(run_id)) or not re.fullmatch(r"\d{1,6}", str(run_attempt)):
        return False
    try:
        directory = Path(_autonomous_state_dir()) / "run-diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.json"
        existing: dict[str, Any] = {}
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 220_000:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict) and str(value.get("run_id")) == str(run_id):
                    existing = value
        except (OSError, ValueError):
            existing = {}
        diagnostics = existing.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, list) else []
        if stage == "controller" and any(
            isinstance(item, dict)
            and str(item.get("run_attempt")) == str(run_attempt)
            and item.get("stage") != "controller"
            for item in diagnostics
        ):
            # Keep the more specific Codex/test/CI diagnosis as the latest
            # visible reason instead of replacing it with the controller wrapper.
            return True
        correlation_id = os.getenv("AUTONOMOUS_ENGINEERING_CORRELATION_ID", "").strip().lower()
        job_name = os.getenv("GITHUB_JOB", "").strip()
        job_id = os.getenv("AUTONOMOUS_ENGINEERING_JOB_ID", "").strip()
        step_id = os.getenv("AUTONOMOUS_ENGINEERING_STEP_ID", "").strip()
        entry = {
            "run_attempt": str(run_attempt),
            "task": task if isinstance(task, int) and not isinstance(task, bool) else None,
            "attempt": attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None,
            "stage": _sanitize_run_diagnostic_text(stage)[:80],
            "category": _sanitize_run_diagnostic_text(category)[:80],
            "summary": _sanitize_run_diagnostic_text(summary)[:800],
            "detail": _sanitize_run_diagnostic_text(detail),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if re.fullmatch(r"[a-f0-9]{32}", correlation_id):
            entry["correlation_id"] = correlation_id
        if job_name:
            entry["job"] = job_name
        if job_id.isdigit():
            entry["job_id"] = int(job_id)
        if step_id:
            entry["step_id"] = step_id
        entry["detail"] = str(entry["detail"])[:6000]
        diagnostics.append(entry)
        payload = {
            "schema_version": 1,
            "run_id": str(run_id),
            "updated_at": entry["recorded_at"],
            "diagnostics": diagnostics[-8:],
            **({"correlation_id": correlation_id} if re.fullmatch(r"[a-f0-9]{32}", correlation_id) else {}),
        }
        temporary = directory / f".{run_id}-{run_attempt}-{os.getpid()}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        try:
            retained = sorted(
                (
                    item for item in directory.glob("*.json")
                    if item.is_file() and not item.is_symlink()
                ),
                key=lambda item: item.stat().st_mtime_ns,
            )
            for expired in retained[:-100]:
                if expired != path:
                    expired.unlink(missing_ok=True)
        except OSError as prune_error:
            print(
                f"Could not prune old autonomous run diagnostics: {type(prune_error).__name__}",
                file=sys.stderr,
            )
        return True
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"Could not persist autonomous run diagnostics: {type(exc).__name__}", file=sys.stderr)
        return False


def changed_paths() -> list[str]:
    return _cycle_workspace.changed_paths(checked=checked, runtime_files=RUNTIME_FILES)


def validate_paths(paths: list[str]) -> None:
    return _cycle_workspace.validate_paths(paths, root=ROOT, allowed=ALLOWED, protected=PROTECTED)


def prepare_commit(task_index: int) -> str:
    return _cycle_workspace.prepare_commit(
        task_index, checked=checked, changed_paths=changed_paths, validate_paths=validate_paths,
    )


def api(token: str, repo: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    return _github_api(token, repo, method, path, payload)


def dispatch_and_wait_all(token: str, repo: str, workflows: tuple[str, ...], branch: str, expected_sha: str) -> None:
    return _github_dispatch_and_wait_all(token, repo, workflows, branch, expected_sha, request=api)


def git_auth_env(token: str) -> dict[str, str]:
    return _github_git_auth_env(token)


def graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    return _github_graphql(token, query, variables)


def find_open_autonomous_pr(token: str, repo: str) -> dict[str, Any] | None:
    return _find_open_autonomous_pr(token, repo, api=api)


def complete_pending_pr(token: str, repo: str, pending: dict[str, Any], run_id: str, task: int) -> dict[str, Any]:
    return _complete_pending_pr(
        token, repo, pending, run_id, task,
        api=api, graphql=graphql, save_pending_pr=save_pending_pr,
        dispatch_and_wait_all=dispatch_and_wait_all,
    )


def complete_merged_pr(token: str, repo: str, pending: dict[str, Any]) -> dict[str, Any]:
    return _complete_merged_pr(
        token, repo, pending, api=api,
        dispatch_and_wait_all=dispatch_and_wait_all,
        clear_checkpoint=clear_checkpoint, clear_pending_state=clear_pending_state,
    )


def publish_pull_request(token: str, repo: str, branch: str, task_index: int, run_id: str, head_sha: str, base_sha: str) -> dict[str, Any]:
    return _publish_pull_request(
        token, repo, branch, task_index, run_id, head_sha, base_sha,
        api=api, checked=checked, git_auth_env=git_auth_env,
        durable_state_lock=durable_state_lock, state_directory=_autonomous_state_dir,
        save_pending_pr_unlocked=_save_pending_pr_unlocked,
        dispatch_and_wait_all=dispatch_and_wait_all,
    )


def publish_pr_subprocess(
    token: str, repo: str, branch: str, task: int, run_id: str, sha: str, base_sha: str,
    timeout_seconds: int = PULL_REQUEST_VALIDATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"GH_TOKEN": token, "GH_REPO": repo})
    result = run([
        sys.executable, str(Path(__file__).resolve()), "publish",
        "--branch", branch, "--task", str(task), "--run-id", run_id,
        "--sha", sha, "--base-sha", base_sha,
    ], env=env, capture=True, timeout_seconds=timeout_seconds)
    if result.returncode:
        raise RuntimeError(
            "Per-task PR publication failed: " + (result.stderr or result.stdout or "")[-4000:]
        )
    try:
        value = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("The PR publisher did not return valid JSON metadata.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The PR publisher returned invalid metadata.")
    return value


def publish_command(args: argparse.Namespace) -> int:
    token = os.environ.pop("GH_TOKEN", "")
    repo = os.environ.pop("GH_REPO", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid workflow token and repository are required.")
    result = publish_pull_request(token, repo, args.branch, args.task, args.run_id, args.sha, args.base_sha)
    print(json.dumps(result, ensure_ascii=False))
    return 0

def run_cycle(args: argparse.Namespace, settings_token: str | None = None) -> int:
    token = os.environ.pop("GH_TOKEN", "")
    if settings_token is None:
        settings_token = os.environ.pop("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "").strip()
    else:
        settings_token = settings_token.strip()
    repo = os.environ.get("GH_REPO", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid workflow token and repository are required.")
    if not settings_token:
        raise RuntimeError("The read-only AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN is required; no task will start.")

    state_dir = _autonomous_state_dir()
    with durable_state_lock(state_dir):
        recover_pending_state_cleanup_unlocked(state_dir)
        open_pull_requests = api(token, repo, "GET", "pulls?state=open&per_page=100")
        if not isinstance(open_pull_requests, list):
            raise RuntimeError("GitHub returned an invalid open pull request listing.")
        def fetch_pending_pr(number: int) -> dict[str, Any]:
            try:
                return api(token, repo, "GET", f"pulls/{number}")
            except RuntimeError as exc:
                if "failed (404)" in str(exc):
                    return {}
                raise

        saved_pending_pr = reconcile_pending_pr_state_unlocked(
            state_dir,
            repo,
            open_pull_requests,
            fetch_pending_pr,
        )
    if saved_pending_pr is not None:
        current_pending = api(token, repo, "GET", f"pulls/{saved_pending_pr['number']}")
        if current_pending.get("merged") is True:
            recovered = complete_merged_pr(token, repo, saved_pending_pr)
            record(
                args.run_id, args.run_attempt, "complete",
                f"وظیفهٔ PR #{recovered['number']} پس از بازبینی مالک پذیرفته شد",
                f"ادغام دستی و CI پس از ادغام روی SHA دقیق {recovered['merge_sha']} تأیید شد",
                status="completed",
            )
            snapshot(args.run_id, args.run_attempt)
            if _dispatch_next_cycle_if_enabled(token, repo, args, settings_token=settings_token):
                print(
                    f"Recovered owner-merged PR #{recovered['number']}; "
                    "next cycle dispatched after live Pause/mode verification."
                )
            else:
                print(
                    f"Recovered owner-merged PR #{recovered['number']}; "
                    "no next cycle was dispatched because live mode/Pause blocked it or could not be verified."
                )
            return 0
        if current_pending.get("state") == "open":
            record(
                args.run_id, args.run_attempt, "recovery",
                f"بازیابی PR #{saved_pending_pr['number']}",
                "اعتبارسنجی مجدد PR باز؛ منتظر بازبینی و ادغام دستی مالک",
            )
            validated = complete_pending_pr(token, repo, saved_pending_pr, args.run_id, int(saved_pending_pr["task"]))
            record(
                args.run_id, args.run_attempt, "publication",
                f"PR #{validated['number']} آمادهٔ بازبینی مالک است",
                "CI دقیق موفق است؛ PR برای بازبینی مالک باز می‌ماند و هیچ ادغام یا وظیفهٔ بعدی به‌صورت خودکار انجام نمی‌شود",
            )
            snapshot(args.run_id, args.run_attempt)
            print(f"PR #{validated['number']} passed exact-head checks and is awaiting owner review: {validated['url']}")
            return 0
        raise RuntimeError("The saved autonomous PR was closed without a confirmed merge; pending task and checkpoint are preserved.")

    started = time.monotonic()
    request_file = ROOT / ".agent-request.json"
    task = 1
    if time.monotonic() - started >= CYCLE_LIMIT - 60:
        raise RuntimeError("The six-hour workflow limit leaves insufficient time to start a task.")

    branch = f"codex/local-engineering-{args.run_id}-{args.run_attempt}-task-{task}"
    checked(["git", "fetch", "origin", "main"], env=git_auth_env(token))
    checked(["git", "switch", "main"])
    checked(["git", "reset", "--hard", "origin/main"])
    base_sha = checked(["git", "rev-parse", "HEAD"])
    checked(["git", "switch", "-c", branch])
    restored_checkpoint = restore_checkpoint(base_sha)
    if restored_checkpoint:
        record(
            args.run_id, args.run_attempt, "recovery",
            f"بازیابی وظیفهٔ {task}",
            "آخرین تغییرات ذخیره‌شده از فضای پایدار ویندوز بازیابی شدند",
        )
    record(args.run_id, args.run_attempt, "coding", f"وظیفهٔ مهندسی {task}", "شروع یا ادامهٔ وظیفهٔ مهندسی از آخرین checkpoint")

    report = Path(os.environ.get("RUNNER_TEMP", str(ROOT))) / f"codex-report-{task}.md"
    attempt = 0
    codex_elapsed_seconds = 0.0
    failure_feedback = ""
    failure_kind = None
    trusted_head = base_sha
    published_pr: dict[str, Any] | None = None

    while True:
        elapsed = time.monotonic() - started
        if elapsed >= CYCLE_LIMIT - 60:
            raise RuntimeError(
                f"Task {task} exhausted this runner window after {attempt} attempt(s); "
                "its checkpoint is retained and it was not published as verified."
            )
        if not TASK_RETRY_POLICY.allows_attempt(attempt, elapsed):
            reason = TASK_RETRY_POLICY.exhaustion_reason(attempt, elapsed)
            record(
                args.run_id, args.run_attempt, "retry",
                f"وظیفهٔ {task} برای اجرای بعدی نگه داشته شد",
                f"بودجهٔ تلاش این اجرا تمام شد: {reason} وضعیت پایدار و checkpoint موجود حفظ می‌شوند",
            )
            raise RuntimeError(
                f"Task {task} reached its bounded retry budget ({reason}); "
                "the task was not discarded or published as verified and will be resumed by recovery."
            )
        if not CODEX_RETRY_POLICY.allows_attempt(attempt, codex_elapsed_seconds):
            reason = CODEX_RETRY_POLICY.exhaustion_reason(attempt, codex_elapsed_seconds)
            record(
                args.run_id, args.run_attempt, "retry",
                f"وظیفهٔ {task} برای بازیابی در اجرای بعدی نگه داشته شد",
                f"بودجهٔ نشست‌های Codex تمام شد: {reason} وضعیت پایدار و checkpoint موجود حفظ می‌شوند",
            )
            raise RuntimeError(
                f"Task {task} reached its bounded Codex execution budget ({reason}); "
                "the task was not discarded or published as verified and will be resumed by recovery."
            )
        attempt += 1
        active_prompt = Path(args.prompt)
        report_only_retry = failure_kind in {
            _cycle_workspace.TaskReportFailureKind.MISSING_TERMINAL_MARKER,
            _cycle_workspace.TaskReportFailureKind.MARKER_NOT_FINAL_LINE,
        }
        if failure_feedback:
            active_prompt = Path(os.environ.get("RUNNER_TEMP", str(ROOT))) / f"codex-retry-prompt-{task}-{attempt}.md"
            original = Path(args.prompt).read_text(encoding="utf-8")
            repair_instruction = (
                "This is a report-finalization repair. Inspect the existing plan, diff, and report; do not repeat completed implementation or rerun broad tests. "
                "If verification evidence already exists, write the concise final report and its single required marker.\n"
                if report_only_retry
                else "Fix the root cause, add or update regression tests, then report TASK_COMPLETE only after your own relevant tests pass.\n"
            )
            active_prompt.write_text(
                original + "\n\n## Required repair after failed verification\n"
                + "This is a fresh session for the SAME task. Do not select or start another task. "
                "do not reset or discard the existing worktree/checkpoint; inspect and preserve all prior task state.\n"
                + failure_feedback + "\n" + repair_instruction,
                encoding="utf-8",
            )
        elif restored_checkpoint:
            active_prompt = Path(os.environ.get("RUNNER_TEMP", str(ROOT))) / f"codex-resume-prompt-{task}.md"
            original = Path(args.prompt).read_text(encoding="utf-8")
            active_prompt.write_text(
                original + "\n\n## Mandatory checkpoint recovery\n"
                + "A durable worktree checkpoint from an interrupted run has been restored. Continue the SAME in-progress task represented by the restored diff and .agent-progress.json. Do not discard or overwrite the restored work. Do not select another task or report QUEUE_EMPTY. Inspect the restored changes, complete the task, add regression tests, and report TASK_COMPLETE only after verification.\n",
                encoding="utf-8",
            )
        report.unlink(missing_ok=True)
        attempt_status_path = report.with_suffix(report.suffix + ".status.json")
        attempt_status_path.unlink(missing_ok=True)
        record(
            args.run_id, args.run_attempt, "coding",
            f"وظیفهٔ {task}؛ تلاش {attempt}",
            f"اجرای نشست تازه برای همان وظیفه؛ تلاش {attempt}",
        )
        watcher = start_checkpoint_watcher(base_sha, args.run_id, task)
        codex_attempt_started = time.monotonic()
        try:
            task_remaining = TASK_RETRY_POLICY.remaining_seconds(time.monotonic() - started)
            codex_remaining = CODEX_RETRY_POLICY.remaining_seconds(codex_elapsed_seconds)
            remaining = min(task_remaining, codex_remaining)
            if remaining <= 0:
                raise RuntimeError("Codex or task execution budget expired before Codex could start; preserving the current task state.")
            attempt_timeout_seconds = min(600, int(remaining)) if report_only_retry else CODEX_TASK_TIMEOUT_SECONDS
            command = [
                sys.executable, "scripts/autonomous_engineering_progress.py", "codex",
                "--run-id", args.run_id, "--run-attempt", args.run_attempt,
                "--model", args.model, "--prompt", str(active_prompt), "--report", str(report),
            ]
            if report_only_retry:
                command.extend(["--timeout-seconds", str(max(1, attempt_timeout_seconds))])
            with timed_stage(f"codex_attempt_{attempt}"):
                result = run(
                    command,
                    capture=True,
                    timeout_seconds=min(attempt_timeout_seconds + 30, remaining),
                )
        finally:
            stop_checkpoint_watcher(watcher, base_sha, args.run_id, task)
            codex_elapsed_seconds += time.monotonic() - codex_attempt_started
        captured_output = _sanitize_run_diagnostic_text(result.stdout or "")
        captured_error = _sanitize_run_diagnostic_text(result.stderr or "")
        if captured_output:
            print(captured_output[-_MAX_RUN_DIAGNOSTIC_CHARS:], flush=True)
        if captured_error:
            print(captured_error[-_MAX_RUN_DIAGNOSTIC_CHARS:], file=sys.stderr, flush=True)
        attempt_status: dict[str, Any] = {}
        try:
            status_payload = json.loads(attempt_status_path.read_text(encoding="utf-8"))
            if isinstance(status_payload, dict) and status_payload.get("schema_version") == 1:
                attempt_status = status_payload
        except (OSError, ValueError):
            attempt_status = {}
        if attempt_status and attempt_status.get("final_agent_message_present") is False:
            turn_completed = attempt_status.get("turn_completed") is True
            turn_failed = attempt_status.get("turn_failed") is True
            event_count = attempt_status.get("jsonl_event_count")
            missing_reason = str(attempt_status.get("missing_message_reason") or (
                "turn_failed_or_tool_error" if turn_failed
                else "turn_incomplete_or_tool_call_pending" if not turn_completed
                else "turn_completed_without_agent_message"
            ))
            summary = (
                "Codex emitted no final agent message; "
                f"reason={missing_reason}, turn_completed={turn_completed}, turn_failed={turn_failed}, "
                f"jsonl_event_count={event_count if isinstance(event_count, int) else 'unknown'}."
            )
            detail = captured_error or captured_output
            persist_run_diagnostic(
                args.run_id, args.run_attempt, task=task, attempt=attempt,
                stage="codex_terminal_event", category="no_last_agent_message",
                summary=summary,
                detail=detail,
            )
            record(
                args.run_id, args.run_attempt, "retry",
                f"وظیفهٔ {task} برای بازیابی نگه داشته شد",
                "Codex پیام نهایی عامل را تولید نکرد؛ علت رویدادهای پایانی ثبت شد و checkpoint حفظ شد",
                status="failed",
            )
            raise RuntimeError(
                summary + " The task/checkpoint is preserved; inspect context exhaustion, incomplete tool calls, and terminal JSONL events before retrying."
            )
        if result.returncode:
            report_detail = _read_report_diagnostic(report)
            diagnostic_detail = _sanitize_run_diagnostic_text(
                "\n".join(part for part in (report_detail, captured_error, captured_output) if part)
            )
            diagnostic_summary = f"Codex process exited with code {result.returncode}."
            persist_run_diagnostic(
                args.run_id, args.run_attempt, task=task, attempt=attempt,
                stage="codex", category="process_exit",
                summary=diagnostic_summary,
                detail=diagnostic_detail,
            )
            failure_kind = None
            failure_feedback = (
                f"Attempt {attempt}: {diagnostic_summary}\n"
                "Sanitized diagnostic from the failed Codex attempt follows; use it to diagnose and repair the SAME task.\n"
                + (diagnostic_detail[-6000:] if diagnostic_detail else "No report or process diagnostic was captured.") + "\n"
                "Inspect the existing worktree and checkpoint before changing files.\n"
            )
            continue

        try:
            marker = task_marker(report)
        except _cycle_workspace.TaskReportError as exc:
            print(f"Codex final report validation failed ({exc.kind.value}): {exc}", file=sys.stderr)
            detail = _read_report_diagnostic(report)
            persist_run_diagnostic(
                args.run_id, args.run_attempt, task=task, attempt=attempt,
                stage="report_validation", category=exc.kind.value,
                summary=str(exc),
                detail=detail,
            )
            if exc.kind == _cycle_workspace.TaskReportFailureKind.NO_LAST_AGENT_MESSAGE:
                record(
                    args.run_id, args.run_attempt, "retry",
                    f"وظیفهٔ {task} برای بازیابی نگه داشته شد",
                    "Codex پیام نهایی عامل را تولید نکرد؛ اجرای کورکورانه تکرار نشد و checkpoint حفظ شد",
                    status="failed",
                )
                raise RuntimeError(
                    "Codex produced no final agent message. The task/checkpoint is preserved; inspect the turn's context limit, incomplete tool call, and terminal JSONL events before retrying."
                ) from exc
            failure_kind = exc.kind
            failure_feedback = (
                f"Attempt {attempt}: report validation failed ({exc.kind.value}): {exc}\n"
                "This is a report-finalization repair for the SAME task, not a new implementation attempt.\n"
                "Inspect the existing plan, diff, and report. Do not repeat completed implementation or broad tests.\n"
                "If evidence is complete, write a concise final report whose last non-empty line is exactly one plain marker with no Markdown, quote, or punctuation: TASK_COMPLETE, QUEUE_EMPTY, TASK_BLOCKED.\n"
                "If work is incomplete, finish only the remaining plan item and verify it.\n"
                + (detail[-4000:] if detail else "The report had no additional diagnostic text.") + "\n"
            )
            record(
                args.run_id, args.run_attempt, "retry",
                f"اصلاح گزارش نهایی وظیفهٔ {task}",
                "گزارش نهایی نشانگر پایانی معتبر نداشت؛ نشست اصلاح گزارش با مهلت کوتاه اجرا می‌شود",
            )
            continue

        if marker == "QUEUE_EMPTY":
            if attempt > 1 or restored_checkpoint:
                raise RuntimeError("Codex reported QUEUE_EMPTY while recovering an unfinished task; refusing to discard the checkpoint.")
            checked(["git", "switch", "main"])
            checked(["git", "branch", "-D", branch])
            clear_checkpoint(args.run_id, task)
            print("Engineering queue is empty.")
            return 0
        if marker == "TASK_BLOCKED":
            blocked_detail = _read_report_diagnostic(report)
            meaningful_detail = "".join(ch for ch in blocked_detail if ch.isalnum())
            if len(meaningful_detail) < 80:
                persist_run_diagnostic(
                    args.run_id, args.run_attempt, task=task, attempt=attempt,
                    stage="report_validation", category="invalid_blocked_report",
                    summary="Codex returned TASK_BLOCKED without a concrete explanation; the same task will be retried.",
                    detail=blocked_detail,
                )
                failure_feedback = (
                    f"Attempt {attempt}: TASK_BLOCKED is not accepted without a substantive explanation.\n"
                    "Continue the SAME task and preserve all checkpoint/worktree changes. Do not switch tasks. "
                    "Either complete the task, or if it truly cannot be completed safely, explain: "
                    "the exact action attempted, the file/command/evidence involved, the concrete blocker, "
                    "why no safe alternative exists, and what is needed to unblock it. "
                    "The explanation must identify the attempted action, concrete evidence, blocker, safe alternatives, and required unblocker.\n"
                )
                record(
                    args.run_id, args.run_attempt, "retry",
                    f"تکمیل توضیح انسداد وظیفهٔ {task}",
                    "گزارش TASK_BLOCKED توضیح کافی نداشت؛ همان وظیفه با حفظ checkpoint دوباره اجرا می‌شود",
                )
                continue
            first_line = next(
                (
                    line.strip() for line in blocked_detail.splitlines()
                    if line.strip().lower().startswith(("concrete blocker:", "blocker:"))
                ),
                next((line.strip() for line in blocked_detail.splitlines() if line.strip()), ""),
            )
            persist_run_diagnostic(
                args.run_id, args.run_attempt, task=task, attempt=attempt,
                stage="codex_decision", category="task_blocked",
                summary=(first_line[:800] if first_line else f"Codex marked task {task} as blocked."),
                detail=blocked_detail,
            )
            raise RuntimeError(f"Codex reported task {task} as blocked; record the blocker and select no unsafe action.")
        if marker != "TASK_COMPLETE":
            failure_feedback = (
                f"Attempt {attempt}: Codex did not return TASK_COMPLETE. "
                "Correct the report and finish this same task.\n"
            )
            continue
        if checked(["git", "rev-parse", "HEAD"]) != trusted_head:
            raise RuntimeError("Codex must not create or rewrite commits; only the trusted controller may commit.")
        if checked(["git", "branch", "--show-current"]) != branch:
            raise RuntimeError("Codex changed the task branch; refusing to publish.")

        record(
            args.run_id, args.run_attempt, "tests",
            f"آزمون وظیفهٔ {task}؛ تلاش {attempt}",
            "اجرای کامل آزمون‌ها پیش از انتشار PR",
        )
        commands = [
            ([sys.executable, "-m", "pytest", "-vv", "--durations=25", "-o", "faulthandler_timeout=60"], TEST_SUITE_TIMEOUT_SECONDS),
            ([sys.executable, "scripts/generate_learning_artifacts.py", "--check"], DEFAULT_COMMAND_TIMEOUT_SECONDS),
            ([sys.executable, "scripts/check_markdown_links.py"], DEFAULT_COMMAND_TIMEOUT_SECONDS),
            ([sys.executable, "scripts/check_import_cycles.py", "--fail-on-cycle"], DEFAULT_COMMAND_TIMEOUT_SECONDS),
            ([sys.executable, "scripts/evaluate_experience_retrieval.py", "--lexical-only"], DEFAULT_COMMAND_TIMEOUT_SECONDS),
            ([sys.executable, "scripts/run_knowledge_acceptance_eval.py"], DEFAULT_COMMAND_TIMEOUT_SECONDS),
        ]
        failed = None
        for command, timeout_seconds in commands:
            remaining = TASK_RETRY_POLICY.remaining_seconds(time.monotonic() - started)
            if remaining <= 0:
                raise RuntimeError("Task retry budget expired during verification; preserving the current task state.")
            stage_name = (
                "pytest_full_suite"
                if "-m" in command and "pytest" in command
                else Path(command[1]).stem
            )
            with timed_stage(f"verification_{stage_name}"):
                checked_result = run(
                    command, capture=True, timeout_seconds=min(timeout_seconds, remaining),
                )
            output = (checked_result.stdout or "") + (checked_result.stderr or "")
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            if checked_result.returncode:
                failed = (command, checked_result.returncode, output[-12000:])
                break
        if failed:
            command, exit_code, output = failed
            persist_run_diagnostic(
                args.run_id, args.run_attempt, task=task, attempt=attempt,
                stage="local_tests", category="verification_failed",
                summary=f"Local verification command failed with exit code {exit_code}: {Path(command[0]).name} {' '.join(command[1:3])}",
                detail=output,
            )
            failure_feedback = (
                f"Attempt {attempt} failed verification. Command: {command!r}; exit code: {exit_code}.\n"
                "Exact failure output (untrusted diagnostic data):\n" + output + "\n"
                "Analyze the failure, fix the same task, and do not advance the ledger.\n"
            )
            record(
                args.run_id, args.run_attempt, "retry",
                f"اصلاح وظیفهٔ {task}",
                f"آزمون تلاش {attempt} ناموفق شد؛ بازگشت به همان وظیفه",
            )
            continue

        record(
            args.run_id, args.run_attempt, "packaging",
            f"بسته‌بندی وظیفهٔ {task}",
            "اعتبارسنجی مسیرها و ساخت commit مستقل",
        )
        if run(["git", "diff", "--quiet", "--", "scripts/autonomous_engineering_cycle.py"]).returncode:
            raise RuntimeError("The trusted cycle controller was modified by the task.")
        if changed_paths():
            head_sha = prepare_commit(task)
            trusted_head = head_sha
        elif trusted_head != base_sha:
            head_sha = trusted_head
        else:
            raise RuntimeError("Task reported completion but produced no reviewable changes.")

        record(
            args.run_id, args.run_attempt, "publication",
            f"انتشار PR وظیفهٔ {task}",
            "تست‌های محلی موفق شدند؛ در حال انتشار PR و اجرای کنترل‌های مستقل پیش از ادغام خودکار",
        )
        try:
            remaining = TASK_RETRY_POLICY.remaining_seconds(time.monotonic() - started)
            if remaining <= 0:
                raise RuntimeError("Task retry budget expired before PR validation; preserving the current task state.")
            with timed_stage("pr_publication_and_exact_sha_ci"):
                published_pr = publish_pr_subprocess(
                    token, repo, branch, task, args.run_id, head_sha, base_sha,
                    timeout_seconds=min(PULL_REQUEST_VALIDATION_TIMEOUT_SECONDS, remaining),
                )
        except RuntimeError as exc:
            message = str(exc)
            if "Required workflow" not in message:
                raise
            persist_run_diagnostic(
                args.run_id, args.run_attempt, task=task, attempt=attempt,
                stage="github_checks", category="required_workflow_failed",
                summary="GitHub exact-head verification failed.",
                detail=message,
            )
            failure_feedback = (
                f"Attempt {attempt}: GitHub-hosted verification failed for candidate {head_sha}.\n"
                "The existing task PR remains open. Review this exact CI failure and repair the SAME task; "
                "do not start another task.\n" + message + "\n"
            )
            record(
                args.run_id, args.run_attempt, "retry",
                f"اصلاح CI وظیفهٔ {task}",
                f"CI گیت‌هاب تلاش {attempt} ناموفق شد؛ اصلاح همان وظیفه",
            )
            continue
        break

    if not isinstance(published_pr, dict):
        raise RuntimeError("Task exited its verification loop without confirmed PR publication.")
    pr_number = published_pr.get("number")
    pr_url = published_pr.get("url")
    pr_head = published_pr.get("head_sha")
    if not isinstance(pr_number, int) or not isinstance(pr_url, str) or not isinstance(pr_head, str):
        raise RuntimeError("The published PR metadata is incomplete.")

    if published_pr.get("merged") is True and published_pr.get("owner_merged") is True:
        accepted = complete_merged_pr(token, repo, published_pr)
        record(
            args.run_id, args.run_attempt, "complete",
            f"وظیفهٔ {task} پس از ادغام دستی مالک پذیرفته شد",
            f"PR #{pr_number} و CI پس از ادغام روی SHA دقیق {accepted['merge_sha']} تأیید شدند",
            status="completed",
        )
        snapshot(args.run_id, args.run_attempt)
        request_file.unlink(missing_ok=True)
        if _dispatch_next_cycle_if_enabled(token, repo, args, settings_token=settings_token):
            print(f"Owner-merged task {task} accepted as PR #{pr_number}; next cycle dispatched from main: {pr_url}")
        else:
            print(
                f"Owner-merged task {task} accepted as PR #{pr_number}; no next cycle was dispatched "
                f"because live mode/Pause blocked it or could not be verified: {pr_url}"
            )
        return 0
    if published_pr.get("validated") is not True or published_pr.get("merged") is not False:
        raise RuntimeError("The candidate PR did not complete exact-head validation and durable owner-review handoff.")
    record(
        args.run_id, args.run_attempt, "publication",
        f"PR #{pr_number} آمادهٔ بازبینی مالک است",
        "تمام کنترل‌های دقیق روی head موفق شدند؛ PR برای تصمیم مالک باز می‌ماند و checkpoint/task تا ادغام دستی حفظ می‌شوند",
    )
    snapshot(args.run_id, args.run_attempt)
    print(f"Task {task} passed exact-head checks and is awaiting owner review; no automatic merge or queue advance: {pr_url}")
    return 0


def _settings_api(settings_token: str, repo: str, path: str) -> Any:
    if not settings_token:
        raise RuntimeError("The read-only AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN is required.")
    return api(settings_token, repo, "GET", path)


def _verify_repository_access(settings_token: str, repo: str) -> None:
    try:
        metadata = _settings_api(settings_token, repo, "")
    except RuntimeError as exc:
        raise RuntimeError(
            "Could not verify repository access; no automatic task will start."
        ) from exc
    if not isinstance(metadata, dict):
        raise RuntimeError("GitHub returned invalid repository metadata; no automatic task will start.")
    actual_repository = str(metadata.get("full_name") or "").strip()
    if actual_repository.casefold() != repo.casefold():
        raise RuntimeError("The GitHub token could not verify the configured repository; no automatic task will start.")


def _verify_variables_read_access(settings_token: str, repo: str) -> None:
    """Prove Variables: read access before interpreting a named-variable 404 as absence."""
    try:
        inventory = _settings_api(settings_token, repo, "actions/variables?per_page=1")
    except RuntimeError as exc:
        raise RuntimeError(
            "Could not verify repository Variables read permission; no automatic task will start."
        ) from exc
    total_count = inventory.get("total_count") if isinstance(inventory, dict) else None
    variables = inventory.get("variables") if isinstance(inventory, dict) else None
    if (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or total_count < 0
        or not isinstance(variables, list)
    ):
        raise RuntimeError(
            "GitHub returned an invalid repository Variables inventory; no automatic task will start."
        )



def _live_pause_enabled(settings_token: str, repo: str) -> bool:
    if not settings_token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid read-only Variables token and repository are required to verify the live Pause setting.")
    _verify_repository_access(settings_token, repo)
    _verify_variables_read_access(settings_token, repo)
    try:
        setting = _settings_api(settings_token, repo, "actions/variables/AUTONOMOUS_ENGINEERING_PAUSED")
    except RuntimeError as exc:
        if "failed (404)" in str(exc):
            return False
        raise RuntimeError(
            "Could not verify the live durable Pause setting; no new task will start."
        ) from exc
    if not isinstance(setting, dict):
        raise RuntimeError("GitHub returned an invalid durable Pause setting; no new task will start.")
    value = str(setting.get("value") or "").strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError("GitHub returned an invalid durable Pause value; no new task will start.")
    return value == "true"


def _live_execution_mode(settings_token: str, repo: str) -> str:
    if not settings_token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid read-only Variables token and repository are required to verify the live execution mode.")
    _verify_repository_access(settings_token, repo)
    _verify_variables_read_access(settings_token, repo)
    try:
        setting = _settings_api(settings_token, repo, "actions/variables/AUTONOMOUS_ENGINEERING_MODE")
    except RuntimeError as exc:
        if "failed (404)" in str(exc):
            return "continuous"
        raise RuntimeError(
            "Could not verify the live execution mode; no automatic task will start."
        ) from exc
    if not isinstance(setting, dict):
        raise RuntimeError("GitHub returned an invalid execution mode; no automatic task will start.")
    value = str(setting.get("value") or "").strip().lower()
    if value not in {"continuous", "scheduled"}:
        raise RuntimeError("GitHub returned an invalid execution mode; no automatic task will start.")
    return value


def _dispatch_next_cycle_if_enabled(
    token: str,
    repo: str,
    args: Any,
    *,
    settings_token: str | None = None,
) -> bool:
    # The live repository setting, not the workflow-start snapshot, is the
    # authority for whether a completed owner-merged task may chain.
    settings_token = (settings_token or os.environ.get("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "")).strip()
    try:
        if _live_execution_mode(settings_token, repo) != "continuous":
            return False
        if _live_pause_enabled(settings_token, repo):
            return False
    except RuntimeError as exc:
        print(f"Live mode/Pause could not be verified; suppressing next-cycle dispatch: {exc}")
        return False
    api(token, repo, "POST", "actions/workflows/autonomous-codex-engineering.yml/dispatches", {
        "ref": "main",
        "inputs": {"manual_task": "", "correlation_id": getattr(args, "correlation_id", "")},
    })
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    cycle = subs.add_parser("run")
    cycle.add_argument("--run-id", required=True)
    cycle.add_argument("--run-attempt", default="1")
    cycle.add_argument("--correlation-id", default="")
    cycle.add_argument("--model", required=True)
    cycle.add_argument("--prompt", required=True)
    cycle.add_argument("--mode", choices=("scheduled", "continuous"), required=True)
    pub = subs.add_parser("publish")
    pub.add_argument("--branch", required=True)
    pub.add_argument("--task", required=True, type=int)
    pub.add_argument("--run-id", required=True)
    pub.add_argument("--sha", required=True)
    pub.add_argument("--base-sha", required=True)
    args = parser.parse_args()
    if args.command == "run" and args.correlation_id:
        if not re.fullmatch(r"[a-f0-9]{32}", args.correlation_id):
            raise SystemExit("A valid 32-character correlation ID is required.")
        os.environ["AUTONOMOUS_ENGINEERING_CORRELATION_ID"] = args.correlation_id
    if args.command == "publish":
        return publish_command(args)
    with repository_execution_lock(args.run_id, args.run_attempt):
        with timed_stage("cycle_total"):
            try:
                token = os.environ.get("GH_TOKEN", "").strip()
                settings_token = os.environ.pop("AUTONOMOUS_ENGINEERING_VARIABLES_TOKEN", "").strip()
                repo = os.environ.get("GH_REPO", "").strip()
                if _live_pause_enabled(settings_token, repo):
                    print("Safe pause became active after repository execution admission; no task was started.")
                    return 0
                live_mode = _live_execution_mode(settings_token, repo)
                event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip().lower()
                try:
                    recovery_attempt = int(os.environ.get("AUTONOMOUS_ENGINEERING_RECOVERY_ATTEMPT", "0"))
                except ValueError as exc:
                    raise RuntimeError("Recovery attempt metadata is invalid; no task will start.") from exc
                if recovery_attempt < 0:
                    raise RuntimeError("Recovery attempt metadata is invalid; no task will start.")
                manual_task = os.environ.get("AUTONOMOUS_ENGINEERING_MANUAL_TASK", "").strip()
                resume_once = os.environ.get("AUTONOMOUS_ENGINEERING_RESUME_ONCE", "false").strip().lower() == "true"
                unmarked_empty_dispatch = (
                    event_name == "workflow_dispatch" and not manual_task and not resume_once
                )
                if live_mode == "scheduled" and (
                    event_name in {"push", "schedule"} or recovery_attempt > 0 or unmarked_empty_dispatch
                ):
                    print("Live execution mode is scheduled; this automatic/recovery trigger will not start a task.")
                    return 0
                return run_cycle(args, settings_token=settings_token)
            except Exception as exc:
                persist_run_diagnostic(
                    args.run_id, args.run_attempt, task=1, attempt=None,
                    stage="controller", category=type(exc).__name__,
                    summary=f"{type(exc).__name__}: {exc}",
                )
                raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Autonomous engineering cycle failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
