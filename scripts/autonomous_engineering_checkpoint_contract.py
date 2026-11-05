from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 3


def build_checkpoint_metadata(
    *,
    base_sha: str,
    run_id: str,
    task: int,
    watcher_pid: int | None,
    updated_at: float,
    patch_bytes: int,
) -> dict[str, Any]:
    """Build the single atomic ownership/readiness record for a checkpoint."""
    return {
        "schema_version": SCHEMA_VERSION,
        "base_sha": base_sha,
        "run_id": run_id,
        "task": task,
        "watcher_pid": watcher_pid,
        "watcher_ready": watcher_pid is not None,
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
) -> bool:
    """Accept readiness only from the exact watcher that atomically saved its snapshot."""
    return (
        isinstance(metadata, dict)
        and metadata.get("schema_version") == SCHEMA_VERSION
        and metadata.get("watcher_ready") is True
        and metadata.get("run_id") == run_id
        and metadata.get("task") == task
        and metadata.get("base_sha") == base_sha
        and metadata.get("watcher_pid") == watcher_pid
    )
