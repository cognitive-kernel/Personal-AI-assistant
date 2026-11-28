"""Remove saved tasks from the active queue under the Runner's ownership locks."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

from app.autonomous_engineering_checkpoint_lock import checkpoint_store_lock
from app.autonomous_engineering_execution_lock import repository_execution_lock
from app.autonomous_engineering_state import durable_state_lock, execution_state_directory
from scripts.autonomous_engineering_checkpoint import CHECKPOINT_INDEX_NAME
from scripts.autonomous_engineering_github import api
from scripts.autonomous_engineering_pull_requests import find_open_autonomous_pr

_LEGACY = {"worktree-checkpoint.json", "worktree-checkpoint.patch", "worktree-checkpoint.stop", "worktree-plan.json"}
_MARKERS = ("pending-manual-task.txt", "pending-manual-task-owner.json", "pending-autonomous-pr.json")
_SAVED = re.compile(r"(?:worktree-checkpoint-[A-Za-z0-9_-]{1,100}-[1-9]\d*\.(?:patch|json|stop|ready|error)|worktree-plan-[A-Za-z0-9_-]{1,100}-[1-9]\d*\.json)")


def clear_queue(state: Path) -> int:
    """Retire the entire runnable queue. An interrupted move can be retried."""
    with durable_state_lock(state, timeout_seconds=2), checkpoint_store_lock(state, timeout_seconds=2):
        cleanup = state / "pending-state-cleanup.json"
        if cleanup.exists() or cleanup.is_symlink():
            raise RuntimeError("A verified post-merge cleanup must finish before the queue can be cleared.")
        saved = sorted(path for path in state.iterdir() if path.name in _LEGACY or _SAVED.fullmatch(path.name))
        for marker in _MARKERS:
            path = state / marker
            if path.exists() or path.is_symlink():
                saved.append(path)
        index = state / CHECKPOINT_INDEX_NAME
        has_index = index.exists() or index.is_symlink()
        for path in [*saved, *([index] if has_index else [])]:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise RuntimeError("Queue state contains an unsafe file entry; nothing was removed.")
        if not saved and not has_index:
            return 0
        archive = Path(tempfile.mkdtemp(prefix="retired-engineering-queue-", dir=state))
        if has_index:
            shutil.copyfile(index, archive / index.name)
        replacement = archive / "inactive-index.json"
        replacement.write_text(
            json.dumps({"schema_version": 1, "active": False, "updated_at": time.time()}) + "\n",
            encoding="utf-8",
        )
        os.replace(replacement, index)
        for path in saved:
            os.replace(path, archive / path.name)
        return len(saved)


def main() -> None:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    repo = os.environ.get("GH_REPO", "")
    token = os.environ.get("GH_TOKEN", "").strip()
    if (
        os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or not re.fullmatch(r"[1-9][0-9]{0,23}", run_id)
        or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or not token
    ):
        raise RuntimeError("Queue removal requires an authorized main-branch Workflow.")
    with repository_execution_lock(run_id, attempt, record_owner=False):
        if find_open_autonomous_pr(token, repo, api=api):
            raise RuntimeError("An Engineering PR is still open. Resolve it before clearing the queue.")
        count = clear_queue(execution_state_directory())
    print(f"Removed {count} saved Engineering files from the active queue.")


if __name__ == "__main__":
    main()
