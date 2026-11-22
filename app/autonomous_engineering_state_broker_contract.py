"""Shared validation rules for the pending-task broker protocol."""
from __future__ import annotations

import re
from typing import Any

MAX_REQUEST_BYTES = 16 * 1024
TASK_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")
REVISION_PATTERN = re.compile(r"^[a-f0-9]{64}$")

# Exact endpoint contract. Mutations require an opaque identity and a listing
# revision; paths and caller-selected filenames are never accepted.
_ENDPOINTS = {
    ("GET", "/v1/tasks/pending"): frozenset(),
    ("GET", "/v1/tasks/archived"): frozenset(),
    ("POST", "/v1/tasks/archive"): frozenset({"task_id", "revision"}),
    ("POST", "/v1/tasks/restore"): frozenset({"task_id", "revision"}),
}


def validate_task_request(method: Any, path: Any, body: Any = None) -> dict[str, str] | None:
    """Validate a task operation before any broker transport or state access."""
    if not isinstance(method, str) or not isinstance(path, str):
        raise ValueError("Unsupported pending-task broker request.")
    key = (method.upper(), path)
    required = _ENDPOINTS.get(key)
    if required is None:
        raise ValueError("Unsupported pending-task broker endpoint.")
    if not required:
        if body is not None:
            raise ValueError("Pending-task list requests do not accept a body.")
        return None
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError("Pending-task mutation body has unsupported fields.")
    task_id = body.get("task_id")
    revision = body.get("revision")
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("Invalid opaque pending-task identifier.")
    if not isinstance(revision, str) or not REVISION_PATTERN.fullmatch(revision):
        raise ValueError("Invalid pending-task revision token.")
    return {"task_id": task_id, "revision": revision}
