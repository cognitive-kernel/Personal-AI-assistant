"""Stable public facade for the modular SQLite task queue.

Storage, lease ownership, and user controls are implemented in separate modules.
Existing callers should continue importing this facade.
"""

from .task_manager_core import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    MAX_TASK_TYPE_CHARS,
    MAX_TASK_PAYLOAD_BYTES,
    MAX_TASK_RECORD_BYTES,
    MAX_TASK_PRIORITY,
    MAX_TASK_ATTEMPTS,
    MAX_TASK_DEDUPE_KEY_CHARS,
    TaskManagerError,
    TaskNotFoundError,
    _now,
    _timestamp,
    _json,
    _task,
)
from .task_queue_store import (
    initialize_task_store,
    create_task,
    get_task,
    get_task_by_dedupe_key,
    list_tasks,
    list_tasks_page,
    list_task_events,
    list_terminal_task_ids,
)
from .task_lease_manager import (
    claim_next_task,
    _owned_running_task,
    assert_task_owner,
    renew_lease,
    save_checkpoint,
    complete_task,
    fail_task,
)
from .task_control_manager import (
    delete_task,
    request_cancel,
    request_pause,
    resume_task,
    get_worker_control,
    update_task_priority,
    reschedule_task,
)

__all__ = [
    "ACTIVE_STATUSES", "TERMINAL_STATUSES", "TaskManagerError", "TaskNotFoundError",
    "initialize_task_store", "create_task", "get_task", "get_task_by_dedupe_key",
    "list_tasks", "list_tasks_page", "list_task_events", "list_terminal_task_ids",
    "claim_next_task", "assert_task_owner", "renew_lease", "save_checkpoint",
    "complete_task", "fail_task", "delete_task", "request_cancel", "request_pause",
    "resume_task", "get_worker_control", "update_task_priority", "reschedule_task",
]
