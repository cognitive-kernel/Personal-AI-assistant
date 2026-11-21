"""Task ownership contracts shared by durable background-work adapters."""

from __future__ import annotations

import json
from typing import Any

from . import task_manager

LEARNING_JOB_TASK_TYPE = "learning.run_job"
TOPIC_QUALITY_TASK_TYPE = "learning.evaluate_topic_quality"


def assert_task_contract(
    connection,
    *,
    task_id: int,
    worker_id: str,
    lease_epoch: int,
    task_type: str,
    payload_fields: dict[str, Any],
):
    """Validate lease fencing and bind a worker to the exact domain payload."""
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task_type must be non-empty text.")
    if not isinstance(payload_fields, dict) or not payload_fields:
        raise ValueError("payload_fields must be a non-empty mapping.")

    row = task_manager.assert_task_owner(
        connection, task_id, worker_id, lease_epoch
    )
    if row["task_type"] != task_type:
        raise task_manager.TaskManagerError(
            "Task ownership contract mismatch: task type does not authorize this operation."
        )
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise task_manager.TaskManagerError(
            "Task ownership contract mismatch: persisted payload is invalid."
        ) from exc
    if not isinstance(payload, dict) or any(
        key not in payload or payload[key] != expected
        for key, expected in payload_fields.items()
    ):
        raise task_manager.TaskManagerError(
            "Task ownership contract mismatch: payload does not authorize this domain operation."
        )
    return row
