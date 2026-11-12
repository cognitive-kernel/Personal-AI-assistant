from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import sysconfig
import time
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
    )
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
    )

try:
    from . import autonomous_engineering_cycle_checkpoints as _cycle_checkpoints
    from . import autonomous_engineering_cycle_workspace as _cycle_workspace
except ImportError:  # Direct script execution from the scripts directory.
    import autonomous_engineering_cycle_checkpoints as _cycle_checkpoints
    import autonomous_engineering_cycle_workspace as _cycle_workspace


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
MARKERS = re.compile(r"(?m)^(TASK_COMPLETE|QUEUE_EMPTY|TASK_BLOCKED)\s*$")
CYCLE_LIMIT = 6 * 60 * 60


def run(command: list[str], *, env: dict[str, str] | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
        check=False, stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def checked(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = run(command, env=env, capture=True)
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
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update({"GH_TOKEN": token, "GH_REPO": repo})
    result = run([
        sys.executable, str(Path(__file__).resolve()), "publish",
        "--branch", branch, "--task", str(task), "--run-id", run_id,
        "--sha", sha, "--base-sha", base_sha,
    ], env=env, capture=True)
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

def run_cycle(args: argparse.Namespace) -> int:
    token = os.environ.pop("GH_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("A valid workflow token and repository are required.")

    saved_pending_pr = load_pending_pr()
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
            if args.mode == "continuous":
                api(token, repo, "POST", "actions/workflows/autonomous-codex-engineering.yml/dispatches", {
                    "ref": "main",
                    "inputs": {"manual_task": ""},
                })
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

    pending_pr = find_open_autonomous_pr(token, repo)
    if pending_pr:
        record(
            args.run_id, args.run_attempt, "recovery",
            f"بازیابی PR #{pending_pr.get('number')}",
            "اعتبارسنجی PR باز و ثبت وضعیت پایدار؛ منتظر بازبینی و ادغام دستی مالک",
        )
        validated = complete_pending_pr(token, repo, pending_pr, args.run_id, 1)
        record(
            args.run_id, args.run_attempt, "publication",
            f"PR #{validated['number']} آمادهٔ بازبینی مالک است",
            "CI دقیق موفق است؛ PR برای بازبینی مالک باز می‌ماند و هیچ ادغام یا وظیفهٔ بعدی به‌صورت خودکار انجام نمی‌شود",
        )
        snapshot(args.run_id, args.run_attempt)
        print(f"PR #{validated['number']} passed exact-head checks and is awaiting owner review: {validated['url']}")
        return 0

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
    failure_feedback = ""
    trusted_head = base_sha
    published_pr: dict[str, Any] | None = None

    while True:
        if time.monotonic() - started >= CYCLE_LIMIT - 60:
            raise RuntimeError(
                f"Task {task} exhausted this runner window after {attempt} attempt(s); "
                "it was not published as verified."
            )
        attempt += 1
        active_prompt = Path(args.prompt)
        if failure_feedback:
            active_prompt = Path(os.environ.get("RUNNER_TEMP", str(ROOT))) / f"codex-retry-prompt-{task}-{attempt}.md"
            original = Path(args.prompt).read_text(encoding="utf-8")
            active_prompt.write_text(
                original + "\n\n## Required repair after failed verification\n"
                + "This is a fresh session for the SAME task. Do not select or start another task.\n"
                + failure_feedback + "\nFix the root cause, add or update regression tests, then report TASK_COMPLETE only after your own relevant tests pass.\n",
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
        record(
            args.run_id, args.run_attempt, "coding",
            f"وظیفهٔ {task}؛ تلاش {attempt}",
            f"اجرای نشست تازه برای همان وظیفه؛ تلاش {attempt}",
        )
        watcher = start_checkpoint_watcher(base_sha, args.run_id, task)
        try:
            result = run([
                sys.executable, "scripts/autonomous_engineering_progress.py", "codex",
                "--run-id", args.run_id, "--run-attempt", args.run_attempt,
                "--model", args.model, "--prompt", str(active_prompt), "--report", str(report),
            ])
        finally:
            stop_checkpoint_watcher(watcher, base_sha, args.run_id, task)
        if result.returncode:
            failure_feedback = (
                f"Attempt {attempt}: Codex exited with code {result.returncode}. "
                "Inspect the current worktree and repair the same task.\n"
            )
            continue

        marker = task_marker(report)
        if marker == "QUEUE_EMPTY":
            if attempt > 1 or restored_checkpoint:
                raise RuntimeError("Codex reported QUEUE_EMPTY while recovering an unfinished task; refusing to discard the checkpoint.")
            checked(["git", "switch", "main"])
            checked(["git", "branch", "-D", branch])
            clear_checkpoint(args.run_id, task)
            print("Engineering queue is empty.")
            return 0
        if marker == "TASK_BLOCKED":
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
            [sys.executable, "-m", "pytest", "-vv", "--durations=25", "-o", "faulthandler_timeout=60"],
            [sys.executable, "scripts/generate_learning_artifacts.py", "--check"],
            [sys.executable, "scripts/check_markdown_links.py"],
            [sys.executable, "scripts/check_import_cycles.py", "--fail-on-cycle"],
            [sys.executable, "scripts/evaluate_experience_retrieval.py", "--lexical-only"],
            [sys.executable, "scripts/run_knowledge_acceptance_eval.py"],
        ]
        failed = None
        for command in commands:
            checked_result = run(command, capture=True)
            output = (checked_result.stdout or "") + (checked_result.stderr or "")
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            if checked_result.returncode:
                failed = (command, checked_result.returncode, output[-12000:])
                break
        if failed:
            command, exit_code, output = failed
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
            published_pr = publish_pr_subprocess(
                token, repo, branch, task, args.run_id, head_sha, base_sha
            )
        except RuntimeError as exc:
            message = str(exc)
            if "Required workflow" not in message:
                raise
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
        if args.mode == "continuous":
            api(token, repo, "POST", "actions/workflows/autonomous-codex-engineering.yml/dispatches", {
                "ref": "main",
                "inputs": {"manual_task": ""},
            })
            print(f"Owner-merged task {task} accepted as PR #{pr_number}; next cycle dispatched from main: {pr_url}")
        else:
            print(f"Owner-merged task {task} accepted as PR #{pr_number}; scheduled mode resumes at its next interval: {pr_url}")
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

def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)
    cycle = subs.add_parser("run")
    cycle.add_argument("--run-id", required=True)
    cycle.add_argument("--run-attempt", default="1")
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
    if args.command == "publish":
        return publish_command(args)
    with repository_execution_lock(args.run_id, args.run_attempt):
        return run_cycle(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Autonomous engineering cycle failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
