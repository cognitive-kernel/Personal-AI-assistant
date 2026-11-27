"""Settings validation and persistence for autonomous engineering."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


def save_settings(
    payload: Any,
    *,
    model_pattern: Any,
    allowed_interval_hours: set[int] | frozenset[int],
    is_codex_context_variant: Callable[[str], bool],
    ollama_status: Callable[[str], dict[str, Any]],
    which: Callable[[str], str | None],
    run_command: Callable[..., Any],
    save_repository_variables: Callable[[str, tuple[tuple[str, str], ...]], None],
) -> dict[str, Any]:
    """Validate local model readiness, authenticate GitHub CLI, then persist settings."""
    model = payload.model.strip()
    if not model_pattern.fullmatch(model):
        raise HTTPException(status_code=422, detail="شناسهٔ مدل Ollama معتبر نیست.")
    if is_codex_context_variant(model):
        raise HTTPException(status_code=422, detail="این مدل نسخهٔ داخلی Context است؛ مدل اصلی Ollama را انتخاب کنید.")
    if payload.interval_hours not in allowed_interval_hours:
        raise HTTPException(status_code=422, detail="فاصلهٔ اجرا باید یکی از ۱، ۲، ۴، ۶، ۱۲ یا ۲۴ ساعت باشد.")
    if payload.execution_mode not in {"scheduled", "continuous"}:
        raise HTTPException(status_code=422, detail="حالت اجرا معتبر نیست.")
    local_model = ollama_status(model)
    if not local_model.get("online"):
        raise HTTPException(status_code=503, detail="سرویس Ollama محلی در دسترس نیست؛ مدل ذخیره نشد.")
    if not local_model.get("model_installed"):
        raise HTTPException(status_code=422, detail="مدل انتخاب‌شده در فهرست مدل‌های نصب‌شدهٔ Ollama وجود ندارد.")
    if not local_model.get("model_metadata_available"):
        raise HTTPException(status_code=422, detail="حداکثر Context مدل از metadata معتبر Ollama قابل تأیید نیست؛ مدل ذخیره نشد.")
    if not local_model.get("model_context_supported"):
        raise HTTPException(
            status_code=422,
            detail="با توجه به metadata مدل و حافظهٔ آزاد فعلی، ظرفیت امن Context قابل محاسبه نیست؛ تنظیمات ذخیره نشد.",
        )
    if not local_model.get("model_supports_tools"):
        raise HTTPException(status_code=422, detail="مدل انتخاب‌شده قابلیت tools را اعلام نکرده و برای Codex قابل استفاده نیست.")
    gh = which("gh")
    if not gh:
        raise HTTPException(status_code=503, detail="GitHub CLI نصب نشده است.")
    auth = run_command([gh, "auth", "status", "--hostname", "github.com"], timeout=8)
    if not auth or auth.returncode != 0:
        raise HTTPException(status_code=503, detail="GitHub CLI احراز هویت نشده است.")
    updates = (
        ("AUTONOMOUS_ENGINEERING_MODEL", model),
        ("AUTONOMOUS_ENGINEERING_INTERVAL_HOURS", str(payload.interval_hours)),
        ("AUTONOMOUS_ENGINEERING_MODE", payload.execution_mode),
    )
    save_repository_variables(gh, updates)
    return {
        "saved": True,
        "model": model,
        "interval_hours": payload.interval_hours,
        "execution_mode": payload.execution_mode,
        "message": "تنظیمات عامل در متغیرهای مخزن GitHub ذخیره شد.",
    }
