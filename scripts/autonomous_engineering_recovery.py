"""GitHub Actions recovery coordinator for the autonomous engineering queue."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .autonomous_engineering_recovery_policy import (
        ADMISSION_REJECTED,
        NON_EXECUTION,
        RETRYABLE,
        UNKNOWN,
        classify_failed_run,
        increment_retryable_failure_streak,
    )
except ImportError:  # Direct script execution from the scripts directory.
    from autonomous_engineering_recovery_policy import (
        ADMISSION_REJECTED,
        NON_EXECUTION,
        RETRYABLE,
        UNKNOWN,
        classify_failed_run,
        increment_retryable_failure_streak,
    )


WORKFLOW_FILE = "autonomous-codex-engineering.yml"
MAX_CONSECUTIVE_FAILURES = 3
PAGE_SIZE = 100


class RecoverySuperseded(RuntimeError):
    """A newer main-branch run owns the next recovery decision."""


class GitHubActionsClient:
    def __init__(self, repository: str, token: str) -> None:
        self.base_url = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=self.headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = response.read(4_000_000)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(4000).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub Actions API failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise RuntimeError(
                f"GitHub Actions API request failed: {type(exc).__name__}: {exc}"
            ) from exc

    def lifecycle_settings(self) -> dict[str, Any]:
        """Read current repository lifecycle variables; never trust trigger-time snapshots."""
        result = self.request("GET", "/actions/variables?per_page=100")
        variables = result.get("variables") if isinstance(result, dict) else None
        if not isinstance(variables, list) or any(not isinstance(item, dict) for item in variables):
            raise RuntimeError("GitHub returned an invalid repository-variable list for lifecycle recovery.")
        values = {
            str(item.get("name")): str(item.get("value", ""))
            for item in variables
            if isinstance(item.get("name"), str)
        }
        mode = values.get("AUTONOMOUS_ENGINEERING_MODE", "continuous").strip().lower()
        enabled_value = values.get("AUTONOMOUS_ENGINEERING_ENABLED", "true").strip().lower()
        paused_value = values.get("AUTONOMOUS_ENGINEERING_PAUSED", "false").strip().lower()
        if mode not in {"continuous", "scheduled"}:
            raise RuntimeError("Autonomous Engineering execution mode is invalid; recovery is blocked.")
        if enabled_value not in {"true", "false"} or paused_value not in {"true", "false"}:
            raise RuntimeError("Autonomous Engineering lifecycle variable is invalid; recovery is blocked.")
        return {
            "mode": mode,
            "enabled": enabled_value == "true",
            "paused": paused_value == "true",
        }

    def jobs(self, run_id: int) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        page = 1
        while True:
            result = self.request(
                "GET", f"/actions/runs/{run_id}/jobs?per_page={PAGE_SIZE}&page={page}"
            )
            batch = result.get("jobs") if isinstance(result, dict) else None
            if not isinstance(batch, list):
                raise RuntimeError(f"GitHub returned an invalid job list for run {run_id}.")
            if any(not isinstance(item, dict) for item in batch):
                raise RuntimeError(f"GitHub returned malformed job metadata for run {run_id}.")
            jobs.extend(batch)
            if len(batch) < PAGE_SIZE:
                return jobs
            page += 1

    def retryable_failure_streak(self, failed_run_id: int, current_jobs: list[dict[str, Any]]) -> int:
        streak = 0
        found_trigger = False
        page = 1
        while True:
            result = self.request(
                "GET",
                f"/actions/workflows/{WORKFLOW_FILE}/runs?branch=main&per_page={PAGE_SIZE}&page={page}",
            )
            batch = result.get("workflow_runs") if isinstance(result, dict) else None
            if not isinstance(batch, list):
                raise RuntimeError("GitHub returned an invalid autonomous workflow-run history.")
            if any(not isinstance(run, dict) for run in batch):
                raise RuntimeError("GitHub returned malformed autonomous workflow-run metadata.")
            ordered = sorted(
                batch,
                key=lambda run: int(run.get("run_number", 0)),
                reverse=True,
            )
            for run in ordered:
                if run.get("head_branch") != "main":
                    continue
                run_id = int(run.get("id", 0))
                if not found_trigger:
                    if run_id != failed_run_id:
                        raise RecoverySuperseded(
                            "A newer main-branch workflow run exists; this recovery event is stale."
                        )
                    found_trigger = True

                conclusion = run.get("conclusion")
                if conclusion in {"success", "cancelled"}:
                    return streak
                if conclusion not in {"failure", "timed_out"}:
                    continue

                run_jobs = current_jobs if run_id == failed_run_id else self.jobs(run_id)
                classification = classify_failed_run(run_jobs)
                try:
                    streak = increment_retryable_failure_streak(streak, classification)
                except ValueError as exc:
                    raise RuntimeError(
                        f"Cannot safely classify failed workflow run {run_id}; "
                        "recovery is blocked without changing durable state."
                    ) from exc
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    return streak

            if len(batch) < PAGE_SIZE:
                break
            page += 1

        if not found_trigger:
            raise RuntimeError(
                f"Triggering failed workflow run {failed_run_id} is missing from main-branch history."
            )
        return streak


def report(title: str, message: str, *, error: bool = False) -> None:
    print(f"::{'error' if error else 'notice'} title={title}::{message.replace(chr(10), ' ')}")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"## {title}\n\n{message}\n")


def main() -> int:
    repository = os.environ.get("GH_REPO", "").strip()
    token = os.environ.get("GH_TOKEN", "").strip()
    try:
        failed_run_id = int(os.environ.get("FAILED_RUN_ID", ""))
    except ValueError:
        failed_run_id = 0
    if not repository or "/" not in repository or not token or failed_run_id < 1:
        report("Recovery configuration invalid", "Repository, token, or triggering run ID is invalid.", error=True)
        return 1

    try:
        client = GitHubActionsClient(repository, token)
        lifecycle = client.lifecycle_settings()
        if lifecycle["mode"] != "continuous":
            report("Recovery not enabled", "Scheduled mode does not automatically retry failed runs.")
            return 0
        if not lifecycle["enabled"]:
            report("Recovery finished", "Autonomous Engineering is disabled; no recovery run was dispatched.")
            return 0
        if lifecycle["paused"]:
            report("Recovery paused", "The durable Pause setting is active; no run was dispatched.")
            return 0

        current_jobs = client.jobs(failed_run_id)
        current_classification = classify_failed_run(current_jobs)
        if current_classification == UNKNOWN:
            message = (
                "The failed run could not be classified from its job/step contract. "
                "Recovery is blocked fail-closed; durable state was not changed."
            )
            report("Recovery blocked by unknown failure", message, error=True)
            return 1

        streak = client.retryable_failure_streak(failed_run_id, current_jobs)
        if current_classification in {ADMISSION_REJECTED, NON_EXECUTION}:
            if streak >= MAX_CONSECUTIVE_FAILURES:
                message = (
                    f"CIRCUIT BREAKER OPEN: {streak} consecutive autonomous task-execution failures "
                    f"reached the limit ({MAX_CONSECUTIVE_FAILURES}). No recovery run was dispatched. "
                    "The durable task/checkpoint is preserved; inspect the first failing execution and "
                    "explicitly resume only after its root cause is corrected."
                )
                report("Circuit breaker OPEN", message, error=True)
                return 1
            if current_classification == ADMISSION_REJECTED:
                message = (
                    "The triggering run failed while persisting a new manual task. "
                    "This is a task-admission rejection, not an autonomous execution failure. "
                    "No retry was dispatched and the existing pending task/owner/checkpoint were left untouched."
                )
            else:
                message = (
                    "The triggering run failed in the schedule gate before task execution. "
                    "It is excluded from the execution-failure streak; no retry was dispatched."
                )
            report("Recovery blocked before task execution", message, error=True)
            return 1
        if streak >= MAX_CONSECUTIVE_FAILURES:
            message = (
                f"CIRCUIT BREAKER OPEN: {streak} consecutive autonomous task-execution failures "
                f"reached the limit ({MAX_CONSECUTIVE_FAILURES}). No recovery run was dispatched. "
                "The durable task/checkpoint is preserved; inspect the first failing execution and "
                "explicitly resume only after its root cause is corrected."
            )
            report("Circuit breaker OPEN", message, error=True)
            return 1

        delay_seconds = min(240, 30 * (2 ** min(max(streak - 1, 0), 3)))
        report(
            "Recovery scheduled",
            f"Observed {streak} consecutive task-execution failure(s); retrying after {delay_seconds} seconds.",
        )
        time.sleep(delay_seconds)
        # Re-read lifecycle variables after backoff so Finish/Pause cannot be
        # bypassed by a recovery job that was already queued.
        lifecycle = client.lifecycle_settings()
        if lifecycle["mode"] != "continuous" or not lifecycle["enabled"] or lifecycle["paused"]:
            report(
                "Recovery suppressed",
                "Autonomous Engineering was paused, finished, or switched to scheduled mode during backoff; no run was dispatched.",
            )
            return 0
        # Re-check ownership after backoff; a newer run may have superseded this event.
        client.retryable_failure_streak(failed_run_id, current_jobs)
        # The history scan can take time; re-read the control flags immediately
        # before dispatch so a concurrent Finish/Pause wins over this retry.
        lifecycle = client.lifecycle_settings()
        if lifecycle["mode"] != "continuous" or not lifecycle["enabled"] or lifecycle["paused"]:
            report(
                "Recovery suppressed",
                "Autonomous Engineering was paused, finished, or switched to scheduled mode before dispatch; no run was sent.",
            )
            return 0
        client.request(
            "POST",
            f"/actions/workflows/{WORKFLOW_FILE}/dispatches",
            {
                "ref": "main",
                "inputs": {
                    "manual_task": "",
                    "recovery_attempt": str(streak),
                },
            },
        )
        report(
            "Recovery dispatched",
            f"Dispatched recovery attempt {streak} from the last accepted main checkpoint.",
        )
        return 0
    except RecoverySuperseded as exc:
        report("Recovery superseded", f"{exc} No duplicate recovery was dispatched.")
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        report(
            "Recovery failed closed",
            f"{type(exc).__name__}: {exc}. No durable task/checkpoint state was modified.",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
