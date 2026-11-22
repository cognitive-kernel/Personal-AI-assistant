"""Lifecycle adapters for background services that do not use TaskRuntime.

Each adapter delegates to its existing owner instead of pretending these
services are controlled by the shared SQLite task queue.
"""
from __future__ import annotations

from typing import Any

from . import backup_scheduler
from .config import defaults


EXTERNAL_MODULE_IDS = frozenset({"ai-learning-browser", "backup-scheduler"})
_ACTIONS = frozenset({"start", "pause", "resume", "finish", "force_stop"})


class ExternalModuleControlError(ValueError):
    """Raised when a separate background service cannot perform a lifecycle action."""


def _counts(*, queued: int = 0, active: int = 0, paused: int = 0, cancelled: int = 0) -> dict[str, int]:
    return {
        "queued": queued,
        "running": active,
        "active": active,
        "paused": paused,
        "cancelled": cancelled,
        "succeeded": 0,
        "failed": 0,
        "pending": queued + paused,
        "total": queued + active + paused + cancelled,
    }


def _learning_status() -> dict[str, Any]:
    from .ai_learning import config, service

    enabled = config.is_enabled()
    if not enabled:
        return {
            "id": "ai-learning-browser",
            "name": "یادگیری با مرورگر و Playwright",
            "description": "کارهای پایدار ChatGPT UI؛ داده‌ها و درس‌های ذخیره‌شده مستقل از کنترل اجرا هستند",
            "state": "stopped",
            "display_state": "stopped",
            "task_counts": _counts(),
            "accepting_tasks": False,
            "disabled_actions": sorted(_ACTIONS),
            "message": "این ماژول با AI_LEARNING_ENABLED غیرفعال شده است.",
        }

    control = service.get_learning_control()
    queued_count = len(service.list_run_ids_by_status("queued"))
    running_count = len(service.list_run_ids_by_status("running"))
    active = bool(control.get("worker_active")) or bool(running_count)
    if control.get("draining"):
        state = "draining" if active else ("finished_pending" if queued_count else "finished")
    elif control.get("paused"):
        forced = str(control.get("reason") or "").startswith("توقف اجباری")
        state = "stopping" if active else ("stopped" if forced else "paused")
    else:
        state = "running"
    return {
        "id": "ai-learning-browser",
        "name": "یادگیری با مرورگر و Playwright",
        "description": "کارهای پایدار ChatGPT UI؛ داده‌ها و درس‌های ذخیره‌شده مستقل از کنترل اجرا هستند",
        "state": state,
        "display_state": state,
        "task_counts": _counts(queued=queued_count, active=running_count),
        "accepting_tasks": not control.get("paused") and not control.get("draining"),
        "disabled_actions": [],
        "message": control.get("reason") or "",
    }


def _backup_status() -> dict[str, Any]:
    status = backup_scheduler.get_backup_scheduler_status()
    active = bool(status.get("backup_active"))
    state = str(status.get("state") or "disabled")
    if state == "stopped":
        state = "stopped"
    return {
        "id": "backup-scheduler",
        "name": "زمان‌بند پشتیبان‌گیری",
        "description": "تهیهٔ نسخهٔ پشتیبان تأییدشده از سه پایگاه داده؛ بدون restore یا حذف",
        "state": state,
        "display_state": "stopping" if active and not status.get("running") else state,
        "task_counts": _counts(active=1 if active else 0),
        "accepting_tasks": bool(status.get("running")),
        "disabled_actions": [] if status.get("configured") else sorted(_ACTIONS),
        "interval_hours": status.get("interval_hours"),
        "last_backup_id": status.get("last_backup_id"),
        "last_backup_status": status.get("last_backup_status"),
        "last_error": status.get("last_error"),
        "message": (
            "برای فعال‌سازی، DATABASE_BACKUP_INTERVAL_HOURS باید بین ۱ تا ۱۶۸ تنظیم شود."
            if not status.get("configured") else
            "توقف زمان‌بند، snapshot در حال اجرا را قطع نمی‌کند؛ ابتدا snapshot جاری کامل می‌شود."
            if active else ""
        ),
    }


def list_external_module_controls() -> list[dict[str, Any]]:
    """Return safe status summaries for independently owned background services."""
    result: list[dict[str, Any]] = []
    try:
        result.append(_learning_status())
    except Exception as exc:
        result.append({
            "id": "ai-learning-browser",
            "name": "یادگیری با مرورگر و Playwright",
            "description": "وضعیت مالک مستقل مرورگر قابل دریافت نیست.",
            "state": "unavailable",
            "display_state": "unavailable",
            "task_counts": _counts(),
            "accepting_tasks": False,
            "disabled_actions": sorted(_ACTIONS),
            "message": f"وضعیت ماژول خوانده نشد: {type(exc).__name__}",
        })
    try:
        result.append(_backup_status())
    except Exception as exc:
        result.append({
            "id": "backup-scheduler",
            "name": "زمان‌بند پشتیبان‌گیری",
            "description": "وضعیت زمان‌بند مستقل قابل دریافت نیست.",
            "state": "unavailable",
            "display_state": "unavailable",
            "task_counts": _counts(),
            "accepting_tasks": False,
            "disabled_actions": sorted(_ACTIONS),
            "message": f"وضعیت ماژول خوانده نشد: {type(exc).__name__}",
        })
    return result


def control_external_module(module_id: str, action: str) -> dict[str, Any]:
    """Delegate lifecycle actions to the service that owns the actual worker."""
    if action not in _ACTIONS:
        raise ExternalModuleControlError("Unsupported module action.")

    if module_id == "ai-learning-browser":
        from .ai_learning import browser_automation, config

        if not config.is_enabled():
            raise ExternalModuleControlError("AI-learning is disabled by AI_LEARNING_ENABLED.")
        operations = {
            "start": browser_automation.resume_learning,
            "resume": browser_automation.resume_learning,
            "pause": browser_automation.pause_learning,
            "finish": browser_automation.finish_learning,
            "force_stop": browser_automation.force_stop_learning,
        }
        result = operations[action](None) if action in {"start", "resume"} else operations[action]()
        module = _learning_status()
        module["action"] = action
        module["message"] = result.get("message") or module.get("message") or "فرمان به مالک اجرای یادگیری ارسال شد."
        module["operation_status"] = result.get("status")
        return module

    if module_id == "backup-scheduler":
        interval = defaults.database_backup_interval_hours
        if action in {"start", "resume"}:
            if interval == 0:
                raise ExternalModuleControlError(
                    "زمان‌بند پشتیبان‌گیری غیرفعال است؛ ابتدا DATABASE_BACKUP_INTERVAL_HOURS را تنظیم کنید."
                )
            started = backup_scheduler.start_backup_scheduler()
            module = _backup_status()
            module["message"] = (
                "زمان‌بند فعال شد؛ نخستین snapshot پس از یک بازهٔ کامل اجرا می‌شود."
                if started else "زمان‌بند از قبل فعال است."
            )
        else:
            timeout = 0.25 if action == "force_stop" else (30.0 if action == "finish" else 5.0)
            stopped = backup_scheduler.stop_backup_scheduler(timeout=timeout)
            module = _backup_status()
            if not stopped:
                module["display_state"] = "stopping"
                module["message"] = (
                    "درخواست توقف ثبت شد؛ snapshot جاری قابل قطع ایمن نیست و پس از تکمیل آن زمان‌بند متوقف می‌شود."
                )
            elif action == "pause":
                module["state"] = module["display_state"] = "paused"
                module["message"] = "زمان‌بند مکث شد؛ snapshot در حال اجرا در صورت وجود کامل شده است."
            elif action == "finish":
                module["state"] = module["display_state"] = "finished"
                module["message"] = "زمان‌بند پس از پایان snapshot جاری خاتمه یافت."
            else:
                module["state"] = module["display_state"] = "stopped"
                module["message"] = "درخواست توقف ثبت شد؛ هیچ snapshot در حال اجرا به‌صورت ناامن قطع نشد."
        module["action"] = action
        return module

    raise ExternalModuleControlError("Unknown external module.")
