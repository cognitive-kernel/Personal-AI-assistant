"""Durable lifecycle controls for background task modules.

The UI controls module admission and cooperative task state; it never kills
Python threads or bypasses task lease ownership.
"""
from __future__ import annotations

from typing import Any

from . import db

MODULES: tuple[dict[str, Any], ...] = (
    {
        "id": "research",
        "name": "پژوهش و جست‌وجو",
        "description": "پاسخ‌های پژوهشی و برنامه‌ریزی جست‌وجو",
        "task_types": ("research.answer", "research.plan"),
    },
    {
        "id": "learning",
        "name": "یادگیری و آموزش",
        "description": "ساخت درس، برنامهٔ درسی، بازبینی و چرخهٔ دانش",
        "task_types": (
            "learning.run_job",
            "learning.plan_curriculum",
            "learning.review_candidate",
            "learning.sandbox_candidate",
            "learning.validate_candidate",
            "learning.review_graph_edge",
        ),
    },
    {
        "id": "cloud-discovery",
        "name": "جست‌وجوی منابع ابری",
        "description": "کشف و ثبت منابع پردازشی؛ بدون provision یا اجرای remote",
        "task_types": ("cloud.discover_resources",),
    },
    {
        "id": "programming",
        "name": "دستیار برنامه‌نویسی",
        "description": "وظایف تولید و بررسی کد",
        "task_types": ("programming.assist",),
    },
    {
        "id": "sandbox",
        "name": "محیط ایزوله و آزمون زبان‌ها",
        "description": "اجرای کنترل‌شدهٔ Python و runnerهای زبان",
        "task_types": ("sandbox.run_python", "sandbox.run_language"),
    },
    {
        "id": "experience",
        "name": "آزمون تجربه‌ها",
        "description": "اجرای آزمون‌های عملی حافظهٔ تجربه",
        "task_types": ("experience.run_test",),
    },
    {
        "id": "factual-verification",
        "name": "راستی‌آزمایی factual",
        "description": "بررسی ادعاهای factual با شواهد و آزمون",
        "task_types": ("factual.verify_claim",),
    },
)

MODULE_BY_ID = {item["id"]: item for item in MODULES}
TASK_TYPE_TO_MODULE = {
    task_type: module["id"]
    for module in MODULES
    for task_type in module["task_types"]
}
MODULE_STATES = frozenset({"running", "paused", "draining", "stopped"})
MODULE_ACTIONS = frozenset({"start", "pause", "resume", "finish", "force_stop"})

TASK_STATUSES = (
    "queued", "running", "pause_requested", "paused", "cancel_requested",
    "cancelled", "succeeded", "failed",
)


class ModuleControlError(ValueError):
    """Raised when a module lifecycle transition is invalid."""


class ModuleNotFoundError(LookupError):
    """Raised when a module ID is not part of the explicit registry."""


def enabled_task_types(connection, task_types: tuple[str, ...]) -> tuple[str, ...]:
    """Filter claim candidates using module state inside the caller's queue transaction."""
    module_ids = tuple(sorted({
        TASK_TYPE_TO_MODULE[item] for item in task_types if item in TASK_TYPE_TO_MODULE
    }))
    if not module_ids:
        return task_types
    placeholders = ",".join("?" for _ in module_ids)
    rows = connection.execute(
        f"SELECT module_id, state FROM module_controls WHERE module_id IN ({placeholders})",
        module_ids,
    ).fetchall()
    states = {str(row["module_id"]): str(row["state"]) for row in rows}
    return tuple(
        task_type for task_type in task_types
        if task_type not in TASK_TYPE_TO_MODULE
        or states.get(TASK_TYPE_TO_MODULE[task_type], "running") == "running"
    )


def _counts_for_module(connection, task_types: tuple[str, ...]) -> dict[str, int]:
    placeholders = ",".join("?" for _ in task_types)
    rows = connection.execute(
        f"""SELECT status, COUNT(*) AS count
            FROM task_queue WHERE task_type IN ({placeholders})
            GROUP BY status""",
        task_types,
    ).fetchall()
    counts = {status: 0 for status in TASK_STATUSES}
    for row in rows:
        counts[str(row["status"])] = int(row["count"])
    counts["total"] = sum(counts[status] for status in TASK_STATUSES)
    counts["active"] = sum(counts[status] for status in (
        "running", "pause_requested", "cancel_requested"
    ))
    counts["pending"] = counts["queued"] + counts["paused"]
    return counts


def list_module_controls() -> list[dict[str, Any]]:
    """Return registry-backed module states and task counts without exposing payloads."""
    with db.learning_connection() as connection:
        rows = connection.execute(
            "SELECT module_id, state, updated_at FROM module_controls"
        ).fetchall()
        stored = {str(row["module_id"]): row for row in rows}
        result = []
        for module in MODULES:
            row = stored.get(module["id"])
            state = str(row["state"]) if row is not None else "running"
            counts = _counts_for_module(connection, module["task_types"])
            display_state = (
                "finished" if state == "draining" and counts["active"] == 0 else state
            )
            result.append({
                "id": module["id"],
                "name": module["name"],
                "description": module["description"],
                "state": state,
                "display_state": display_state,
                "updated_at": str(row["updated_at"]) if row is not None else None,
                "task_counts": counts,
                "accepting_tasks": state == "running",
                "can_force_stop": state != "stopped" or counts["active"] > 0 or counts["pending"] > 0,
            })
    return result


def control_module(module_id: str, action: str) -> dict[str, Any]:
    """Apply one atomic module lifecycle transition and its queued-task effects.

    - start: accept new work; previously paused tasks remain paused.
    - pause: pause queued work and request a safe checkpoint from active tasks.
    - resume: accept work and resume paused/pause-requested tasks.
    - finish: drain active work; leave queued work untouched and unclaimed.
    - force_stop: cancel queued work and request cooperative cancellation of active work.
    """
    module = MODULE_BY_ID.get(module_id)
    if module is None:
        raise ModuleNotFoundError(f"Unknown module: {module_id}")
    if action not in MODULE_ACTIONS:
        raise ModuleControlError("Unsupported module action.")
    task_types = module["task_types"]
    placeholders = ",".join("?" for _ in task_types)

    with db.learning_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if action == "pause":
            next_state = "paused"
            connection.execute(
                f"""UPDATE task_queue SET status='paused', updated_at=CURRENT_TIMESTAMP
                    WHERE task_type IN ({placeholders}) AND status='queued'""",
                task_types,
            )
            connection.execute(
                f"""UPDATE task_queue SET status='pause_requested', updated_at=CURRENT_TIMESTAMP
                    WHERE task_type IN ({placeholders}) AND status='running'""",
                task_types,
            )
        elif action == "resume":
            next_state = "running"
            connection.execute(
                f"""UPDATE task_queue SET status='queued', updated_at=CURRENT_TIMESTAMP
                    WHERE task_type IN ({placeholders}) AND status='paused'""",
                task_types,
            )
            connection.execute(
                f"""UPDATE task_queue SET status='running', updated_at=CURRENT_TIMESTAMP
                    WHERE task_type IN ({placeholders}) AND status='pause_requested'""",
                task_types,
            )
        elif action == "finish":
            next_state = "draining"
        elif action == "force_stop":
            next_state = "stopped"
            connection.execute(
                f"""UPDATE task_queue SET status='cancelled', lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE task_type IN ({placeholders}) AND status IN ('queued','paused')""",
                task_types,
            )
            connection.execute(
                f"""UPDATE task_queue SET status='cancel_requested', updated_at=CURRENT_TIMESTAMP
                    WHERE task_type IN ({placeholders}) AND status IN ('running','pause_requested')""",
                task_types,
            )
        else:  # start
            next_state = "running"

        connection.execute(
            """INSERT INTO module_controls(module_id, state, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(module_id) DO UPDATE SET
                   state=excluded.state, updated_at=CURRENT_TIMESTAMP""",
            (module_id, next_state),
        )

    item = next(entry for entry in list_module_controls() if entry["id"] == module_id)
    item["action"] = action
    item["message"] = {
        "start": "ماژول فعال شد؛ وظایف متوقف‌شده بدون فرمان ادامه، خودکار از سر گرفته نمی‌شوند.",
        "pause": "مکث ثبت شد؛ وظایف فعال در نقطهٔ کنترل امن متوقف و checkpoint آن‌ها حفظ می‌شود.",
        "resume": "ادامه ثبت شد؛ وظایف مکث‌شده دوباره وارد صف می‌شوند.",
        "finish": "پایان تدریجی ثبت شد؛ کارهای فعال اجازهٔ اتمام دارند و وظایف جدید آغاز نمی‌شوند.",
        "force_stop": "درخواست توقف اجباری ثبت شد؛ وظایف صف‌شده لغو و وظایف فعال به‌صورت تعاونی لغو می‌شوند.",
    }[action]
    if action == "force_stop":
        item["force_stop_semantics"] = (
            "Python worker threads are not killed unsafely; active handlers stop when they "
            "observe cancellation at a checkpoint or heartbeat."
        )
    return item
