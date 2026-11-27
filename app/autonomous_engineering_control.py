"""Engineering workflow control orchestration; transport and readiness are injected by the HTTP adapter."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from typing import Any

from fastapi import HTTPException

from .autonomous_engineering_contracts import (
    ACTIVE_RUN_STATES,
    CORRELATION_ID_PATTERN as _CORRELATION_ID_PATTERN,
    CancelRequest,
    ManualTaskRequest,
)
from .autonomous_engineering_dispatch_lock import DispatchAdmissionError
from .autonomous_engineering_state_broker import StateBrokerConflict, StateBrokerError

CONTROL_LOCK = threading.Lock()
_STATUS_LOGGER = logging.getLogger(__name__)

def dispatch_with_admission(payload: ManualTaskRequest | None, *, correlation_id: str, resume_once: bool = False, _DISPATCH_REQUEST_LOCK, dispatch_admission_lock, _dispatch_autonomous_engineering_locked) -> dict[str, Any]:
    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="درخواست اجرای دیگری در حال ثبت است؛ از ارسال هم‌زمان در چند زبانه خودداری کنید.",
            headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_IN_PROGRESS", "X-Retryable": "false"},
        )
    try:
        try:
            with dispatch_admission_lock() as admission:
                try:
                    result = _dispatch_autonomous_engineering_locked(
                        payload,
                        correlation_id=correlation_id,
                        resume_once=resume_once,
                    )
                except HTTPException as exc:
                    error_code = (exc.headers or {}).get("X-Error-Code")
                    if error_code in {
                        "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN",
                        "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN",
                    }:
                        admission["accepted"] = True
                    raise
                if result.get("accepted") is True:
                    admission["accepted"] = True
                return result
        except StateBrokerConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="درخواست اجرای دیگری در حال ثبت است یا پنجرهٔ جلوگیری از اجرای تکراری هنوز فعال است؛ وضعیت را بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_RECENTLY_ACCEPTED", "X-Retryable": "false"},
            ) from exc
        except (StateBrokerError, DispatchAdmissionError) as exc:
            _STATUS_LOGGER.error("Persistent dispatch admission unavailable; correlation_id=%s", correlation_id)
            raise HTTPException(
                status_code=503,
                detail="قفل پایدار کنترل ارسال در دسترس یا معتبر نیست؛ برای جلوگیری از اجرای تکراری، درخواست متوقف شد.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_ADMISSION_UNAVAILABLE", "X-Retryable": "false"},
            ) from exc
        except RuntimeError as exc:
            _STATUS_LOGGER.error("Autonomous dispatch preflight failed closed; correlation_id=%s", correlation_id)
            raise HTTPException(
                status_code=503,
                detail="پیش‌بررسی اجرای عامل قابل تأیید نیست؛ هیچ Workflow جدیدی ارسال نشد.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "AUTONOMOUS_DISPATCH_PREFLIGHT_FAILED", "X-Retryable": "false"},
            ) from exc
    finally:
        _DISPATCH_REQUEST_LOCK.release()


def dispatch_locked(
    payload: ManualTaskRequest | None = None,
    *,
    correlation_id: str | None = None,
    resume_once: bool = False,
    collect_status, pending_work_status, _authenticated_gh, _set_autonomous_engineering_pause, _repository, _WORKFLOW, _run, _has_open_autonomous_pr, _sanitize_workflow_log, which,
) -> dict[str, Any]:
    if not isinstance(correlation_id, str) or not _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        correlation_id = uuid.uuid4().hex
    manual_task = payload.task.strip() if payload else ""
    if "\x00" in manual_task:
        raise HTTPException(status_code=422, detail="متن وظیفه شامل نویسهٔ نامعتبر است.")
    readiness = collect_status()
    if not readiness.get("github", {}).get("runs_available", False):
        raise HTTPException(status_code=503, detail="وضعیت اجراهای GitHub قابل دریافت نیست؛ اجرای جدید به‌صورت ایمن متوقف شد.")
    if readiness.get("paused", False) and not resume_once:
        raise HTTPException(status_code=409, detail="صف مهندسی در وضعیت توقف ایمن است؛ ابتدا ادامه را فعال کنید.")
    if not readiness["ready"]:
        raise HTTPException(status_code=409, detail="پیش‌نیازهای عامل کامل نیست؛ ابتدا وضعیت محلی را بررسی کنید.")
    pending = readiness.get("pending_work")
    if manual_task and not isinstance(pending, dict):
        pending = pending_work_status()
    if manual_task and (not isinstance(pending, dict) or pending.get("available") is not True):
        raise HTTPException(status_code=503, detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ وظیفهٔ جدید ارسال نشد.")
    if not manual_task and isinstance(pending, dict) and pending.get("available") is not True:
        raise HTTPException(status_code=503, detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ اجرای ادامه متوقف شد.")
    if not isinstance(pending, dict):
        checks = readiness.get("checks", {})
        if isinstance(checks, dict) and checks.get("durable_state_available") is False:
            raise HTTPException(status_code=503, detail="وضعیت پایدار وظایف و checkpoint قابل بررسی نیست؛ اجرای ادامه متوقف شد.")
    checkpoint = pending.get("checkpoint") if isinstance(pending, dict) and isinstance(pending.get("checkpoint"), dict) else {}
    if checkpoint.get("available") is True and checkpoint.get("verified") is not True:
        raise HTTPException(status_code=409, detail="checkpoint ذخیره‌شده وجود دارد اما یکپارچگی آن قابل تأیید نیست؛ اجرا متوقف شد تا تغییرات قبلی حفظ شوند.")
    if manual_task and pending.get("blocked"):
        if checkpoint.get("available") is True:
            detail = f"یک checkpoint تأییدشده برای اجرای {checkpoint.get('run_id') or 'نامشخص'} و وظیفهٔ {checkpoint.get('task', 1)} باقی مانده است. درخواست جدید پذیرفته نشد؛ اجرای بدون متن وظیفه همان کار را ادامه می‌دهد."
        else:
            detail = "یک تسک، PR یا عملیات پاک‌سازی قبلی هنوز تعیین‌تکلیف نشده است؛ متن وظیفهٔ جدید پذیرفته نشد."
        raise HTTPException(status_code=409, detail=detail)
    active_runs = readiness.get("github", {}).get("recent_runs", [])
    has_active_run = any(
        isinstance(item, dict)
        and item.get("status") in ACTIVE_RUN_STATES
        for item in active_runs
    )
    if resume_once and not readiness.get("paused", False) and not has_active_run:
        raise HTTPException(
            status_code=409,
            detail="صف مهندسی در وضعیت توقف نیست و اجرای فعالی برای ادامه وجود ندارد؛ درخواست Resume تکراری ارسال نشد.",
            headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_NOT_PAUSED", "X-Retryable": "false"},
        )
    if resume_once and has_active_run:
        gh = _authenticated_gh()
        if not _set_autonomous_engineering_pause(gh, False):
            restored = _set_autonomous_engineering_pause(gh, True)
            raise HTTPException(
                status_code=502,
                detail=(
                    "برداشتن توقف برای اجرای فعال تأیید نشد؛ وضعیت توقف دوباره ثبت شد."
                    if restored
                    else "برداشتن توقف و بازگردانی آن در GitHub هر دو نامشخص‌اند؛ وضعیت را فوراً بازخوانی کنید."
                ),
                headers={
                    "X-Correlation-ID": correlation_id,
                    "X-Error-Code": "WORKFLOW_RESUME_STATE_UPDATE_FAILED" if restored else "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED",
                    "X-Retryable": "false",
                },
            )
        return {
            "accepted": True,
            "repository": _repository(),
            "workflow": _WORKFLOW,
            "manual_task_submitted": False,
            "resume_once": True,
            "dispatch_status": "existing_run_active",
            "paused": False,
            "correlation_id": correlation_id,
            "message": "توقف برداشته شد؛ یک اجرای عامل از قبل فعال است، بنابراین برای جلوگیری از اجرای تکراری Workflow تازه‌ای ارسال نشد.",
        }
    if has_active_run:
        raise HTTPException(status_code=409, detail="یک اجرای عامل در حال انجام است؛ ابتدا وضعیت آن را بررسی کنید.")
    gh = which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    if manual_task and _has_open_autonomous_pr(gh):
        raise HTTPException(status_code=409, detail="یک PR مهندسی خودکار هنوز باز است؛ ابتدا همان PR را بازبینی و تعیین‌تکلیف کنید.")
    command = [gh, "workflow", "run", _WORKFLOW, "--repo", _repository(), "--ref", "main"]
    workflow_input = None
    if manual_task:
        # Keep owner-provided task text out of argv; gh reads workflow inputs from stdin.
        command.append("--json")
        inputs = {"correlation_id": correlation_id, "manual_task": manual_task}
        if resume_once:
            inputs["resume_once"] = True
        workflow_input = json.dumps(inputs, ensure_ascii=False)
    else:
        command.extend(["--raw-field", f"correlation_id={correlation_id}", "--raw-field", "manual_task="])
        if resume_once:
            command.extend(["--raw-field", "resume_once=true"])
            if not _set_autonomous_engineering_pause(gh, False):
                restored = _set_autonomous_engineering_pause(gh, True)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "برداشتن توقف تأیید نشد؛ Workflow ارسال نشد و توقف دوباره ثبت شد."
                        if restored
                        else "برداشتن توقف تأیید نشد و بازگردانی آن نیز نامشخص است؛ Workflow ارسال نشد، وضعیت را فوراً بازخوانی کنید."
                    ),
                    headers={
                        "X-Correlation-ID": correlation_id,
                        "X-Error-Code": "WORKFLOW_RESUME_STATE_UPDATE_FAILED" if restored else "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED",
                        "X-Retryable": "false",
                    },
                )
    try:
        if workflow_input is not None:
            result = _run(command, timeout=20, raise_os_error=True, input_text=workflow_input)
        else:
            result = _run(command, timeout=20, raise_os_error=True)
    except OSError as exc:
        if resume_once and not _set_autonomous_engineering_pause(gh, True):
            raise HTTPException(
                status_code=502,
                detail="ارسال Workflow آغاز نشد و بازگردانی توقف در GitHub نیز تأیید نشد؛ وضعیت را فوراً بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED", "X-Retryable": "false"},
            ) from exc
        _STATUS_LOGGER.error("Autonomous workflow dispatch could not start; correlation_id=%s", correlation_id)
        raise HTTPException(status_code=503, detail="GitHub CLI اجرا نشد؛ درخواست به GitHub ارسال نشده است و می‌توانید پس از بررسی نصب و دسترسی، دوباره تلاش کنید. شناسهٔ پیگیری: " + correlation_id, headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_LAUNCH_FAILED" if resume_once else "WORKFLOW_DISPATCH_LAUNCH_FAILED", "X-Retryable": "true"}) from exc
    if result is None:
        _STATUS_LOGGER.warning("Autonomous workflow dispatch outcome is unknown; correlation_id=%s", correlation_id)
        raise HTTPException(status_code=502, detail="نتیجهٔ ارسال Workflow نامشخص است؛ ممکن است GitHub درخواست را دریافت کرده باشد. پیش از تلاش مجدد، سوابق اجرا را بازخوانی کنید. شناسهٔ پیگیری: " + correlation_id, headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_OUTCOME_UNKNOWN" if resume_once else "WORKFLOW_DISPATCH_OUTCOME_UNKNOWN", "X-Retryable": "false"})
    if result.returncode != 0:
        if resume_once and not _set_autonomous_engineering_pause(gh, True):
            raise HTTPException(
                status_code=502,
                detail="GitHub ارسال Workflow را رد کرد اما بازگردانی توقف نیز تأیید نشد؛ وضعیت را فوراً بازخوانی کنید.",
                headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_PAUSE_ROLLBACK_FAILED", "X-Retryable": "false"},
            )
        safe_stderr = _sanitize_workflow_log(str(result.stderr or ""))
        stderr_fingerprint = hashlib.sha256(safe_stderr.encode("utf-8")).hexdigest()[:16] if safe_stderr else "empty"
        _STATUS_LOGGER.error("Autonomous workflow dispatch failed; correlation_id=%s exit_code=%s stderr_sha256=%s", correlation_id, result.returncode, stderr_fingerprint)
        raise HTTPException(status_code=502, detail=f"ارسال Workflow ناموفق بود (exit code {result.returncode}). پیش از تلاش مجدد، سوابق اجرا را بازخوانی کنید. شناسهٔ پیگیری: " + correlation_id, headers={"X-Correlation-ID": correlation_id, "X-Error-Code": "WORKFLOW_RESUME_DISPATCH_FAILED" if resume_once else "WORKFLOW_DISPATCH_FAILED", "X-Retryable": "false"})
    return {
        "accepted": True,
        "repository": _repository(),
        "workflow": _WORKFLOW,
        "manual_task_submitted": bool(manual_task),
        "resume_once": bool(resume_once),
        "dispatch_status": "resume_once_dispatched" if resume_once else "dispatched",
        "paused": False if resume_once else readiness.get("paused", False),
        "correlation_id": correlation_id,
        "message": "ادامه از وضعیت ذخیره‌شده درخواست شد؛ در حالت زمان‌بندی‌شده این اجرا یک‌باره است." if resume_once
            else "وظیفهٔ دستی ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید." if manual_task
            else "درخواست اجرای چرخهٔ خودکار ارسال شد؛ شروع واقعی را در GitHub Actions تأیید کنید.",
    }


def cancel_locked(payload: CancelRequest, *, _authenticated_gh, _verified_workflow_run, _run, _repository) -> dict[str, Any]:
    gh = _authenticated_gh()
    run = _verified_workflow_run(gh, payload.run_id)
    trusted_run_id = int(run["id"])
    status = run.get("status")
    if status not in ACTIVE_RUN_STATES:
        raise HTTPException(status_code=409, detail="اجرای انتخاب‌شده دیگر فعال نیست؛ وضعیت را بازخوانی کنید.")
    if status == "in_progress":
        details = _run(
            [gh, "run", "view", str(trusted_run_id), "--repo", _repository(), "--json", "jobs"],
            timeout=5,
        )
        if not details or details.returncode != 0:
            raise HTTPException(status_code=502, detail="وضعیت jobهای اجرای انتخاب‌شده قابل تأیید نیست؛ لغو ایمن متوقف شد.")
        try:
            jobs_payload = json.loads(details.stdout)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="جزئیات jobهای اجرای انتخاب‌شده معتبر نیست.") from exc
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
        if not isinstance(jobs, list):
            raise HTTPException(status_code=502, detail="فهرست jobهای اجرای انتخاب‌شده در دسترس نیست.")
        codex_jobs = [
            job for job in jobs
            if isinstance(job, dict) and str(job.get("name") or "").strip().casefold() == "codex"
        ]
        if len(codex_jobs) > 1:
            raise HTTPException(status_code=502, detail="بیش از یک job با مالکیت Codex در اجرای انتخاب‌شده گزارش شد؛ لغو ایمن ممکن نیست.")
        if not codex_jobs or str(codex_jobs[0].get("status") or "").strip().casefold().replace("-", "_") not in ACTIVE_RUN_STATES:
            raise HTTPException(status_code=409, detail="در اجرای انتخاب‌شده job فعال Codex وجود ندارد؛ برای جلوگیری از لغو gate یا مرحلهٔ نامرتبط، عملیات متوقف شد.")
    cancelled = _run([gh, "run", "cancel", str(trusted_run_id), "--repo", _repository()], timeout=12)
    if not cancelled or cancelled.returncode != 0:
        raise HTTPException(status_code=502, detail="لغو اجرای انتخاب‌شده توسط GitHub تأیید نشد.")
    return {"accepted": True, "run_id": trusted_run_id, "message": f"درخواست لغو اجرای #{trusted_run_id} به GitHub ارسال شد."}


def set_pause(gh: str, paused: bool, *, _run, _repository) -> bool:
    try:
        result = _run(
            [
                gh,
                "variable",
                "set",
                "AUTONOMOUS_ENGINEERING_PAUSED",
                "--body",
                "true" if paused else "false",
                "--repo",
                _repository(),
            ],
            timeout=15,
        )
    except OSError:
        return False
    return bool(result and result.returncode == 0)


def pause(*, _DISPATCH_REQUEST_LOCK, which, _run, _repository) -> dict[str, Any]:
    if not _DISPATCH_REQUEST_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="درخواست کنترل اجرای دیگری در حال انجام است؛ وضعیت را بازخوانی کنید.", headers={"X-Error-Code": "AUTONOMOUS_PAUSE_IN_PROGRESS", "X-Retryable": "false"})
    try:
        gh = which("gh")
        if not gh:
            raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
        auth = _run([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
        if not auth or auth.returncode != 0:
            raise HTTPException(status_code=503, detail="دسترسی GitHub در حساب برنامه آماده نیست.")
        result = _run([gh, "variable", "set", "AUTONOMOUS_ENGINEERING_PAUSED", "--body", "true", "--repo", _repository()], timeout=15)
        if not result or result.returncode != 0:
            raise HTTPException(status_code=502, detail="ثبت توقف ایمن در GitHub ناموفق بود.")
        return {"accepted": True, "paused": True, "message": "توقف ایمن ثبت شد؛ وظیفهٔ جاری تا مرز امن ادامه می‌یابد و وظیفهٔ جدید آغاز نمی‌شود."}
    finally:
        _DISPATCH_REQUEST_LOCK.release()
