from __future__ import annotations

import math
from typing import Any

SCHEMA_VERSION = 4


def build_checkpoint_metadata(
    *,
    base_sha: str,
    run_id: str,
    task: int,
    watcher_pid: int | None,
    updated_at: float,
    patch_bytes: int,
    watcher_started_at: float | None = None,
) -> dict[str, Any]:
    """Build the atomic checkpoint record and bind readiness to an OS process identity."""
    valid_process_identity = (
        isinstance(watcher_pid, int)
        and not isinstance(watcher_pid, bool)
        and watcher_pid > 0
        and isinstance(watcher_started_at, (int, float))
        and not isinstance(watcher_started_at, bool)
        and math.isfinite(watcher_started_at)
        and watcher_started_at > 0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "base_sha": base_sha,
        "run_id": run_id,
        "task": task,
        "watcher_pid": watcher_pid,
        "watcher_started_at": float(watcher_started_at) if valid_process_identity else None,
        "watcher_ready": valid_process_identity,
        "updated_at": updated_at,
        "patch_bytes": patch_bytes,
    }


def metadata_confirms_watcher_ready(
    metadata: Any,
    *,
    base_sha: str,
    run_id: str,
    task: int,
    watcher_pid: int,
    watcher_started_at: float | None = None,
) -> bool:
    """Accept readiness only from the exact run/task/base and a versioned OS process identity."""
    started_at = metadata.get("watcher_started_at") if isinstance(metadata, dict) else None
    return (
        isinstance(metadata, dict)
        and metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("watcher_ready") is True
        and metadata.get("run_id") == run_id
        and metadata.get("task") == task
        and metadata.get("base_sha") == base_sha
        and metadata.get("watcher_pid") == watcher_pid
        and isinstance(started_at, (int, float))
        and not isinstance(started_at, bool)
        and math.isfinite(started_at)
        and started_at > 0
        and (
            watcher_started_at is None
            or (
                isinstance(watcher_started_at, (int, float))
                and not isinstance(watcher_started_at, bool)
                and math.isfinite(watcher_started_at)
                and abs(float(started_at) - float(watcher_started_at)) <= 0.02
            )
        )
    )
