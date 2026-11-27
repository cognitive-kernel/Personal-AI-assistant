"""Structured API error handling for Autonomous Engineering routes."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .autonomous_engineering_contracts import (
    CORRELATION_ID_PATTERN as _CORRELATION_ID_PATTERN,
    HTTP_ERROR_CODES as _HTTP_ERROR_CODES,
    RETRYABLE_HTTP_STATUSES as _RETRYABLE_HTTP_STATUSES,
)

_STATUS_LOGGER = logging.getLogger(__package__ + ".autonomous_engineering")


def _structured_error_response(
    *, status_code: int, message: str, detail: Any, headers: dict[str, str] | None = None,
    code: str | None = None, retryable: bool | None = None, safe_details: dict[str, Any] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    correlation_id = response_headers.get("X-Correlation-ID") or uuid.uuid4().hex
    error_code = response_headers.get("X-Error-Code") or code or _HTTP_ERROR_CODES.get(
        status_code, "AUTONOMOUS_ENGINEERING_HTTP_ERROR"
    )
    retry_header = response_headers.get("X-Retryable")
    can_retry = (
        retry_header.casefold() == "true" if retry_header is not None
        else (status_code in _RETRYABLE_HTTP_STATUSES if retryable is None else retryable)
    )
    response_headers.update({
        "X-Correlation-ID": correlation_id,
        "X-Error-Code": error_code,
        "X-Retryable": "true" if can_retry else "false",
    })
    _STATUS_LOGGER.warning(
        "Autonomous Engineering API error; status_code=%s code=%s retryable=%s correlation_id=%s",
        status_code,
        error_code,
        can_retry,
        correlation_id,
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": error_code,
            "message": message,
            "correlation_id": correlation_id,
            "retryable": can_retry,
            "safe_details": safe_details if isinstance(safe_details, dict) else {},
        },
        headers=response_headers,
    )


class AutonomousEngineeringAPIRoute(APIRoute):
    """Keep all Autonomous Engineering API failures on one safe diagnostic contract."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def structured_handler(request):
            supplied_id = str(request.headers.get("X-Correlation-ID") or "").strip().lower()
            correlation_id = supplied_id if _CORRELATION_ID_PATTERN.fullmatch(supplied_id) else uuid.uuid4().hex
            request.state.correlation_id = correlation_id
            try:
                response = await original_handler(request)
            except HTTPException as exc:
                message = exc.detail if isinstance(exc.detail, str) else "درخواست ناموفق بود."
                headers = dict(exc.headers or {})
                headers.setdefault("X-Correlation-ID", correlation_id)
                safe_diagnostics = getattr(exc, "safe_details", None)
                if isinstance(safe_diagnostics, dict) and safe_diagnostics:
                    _STATUS_LOGGER.error(
                        "Autonomous Engineering operation diagnostic; status_code=%s code=%s correlation_id=%s safe_details=%s",
                        exc.status_code,
                        headers.get("X-Error-Code") or _HTTP_ERROR_CODES.get(exc.status_code, "unknown"),
                        correlation_id,
                        json.dumps(safe_diagnostics, ensure_ascii=False, sort_keys=True)[:700],
                    )
                return _structured_error_response(
                    status_code=exc.status_code,
                    message=message,
                    detail=message,
                    headers=headers,
                    safe_details=getattr(exc, "safe_details", None),
                )
            except RequestValidationError as exc:
                errors = []
                for error in exc.errors():
                    location = error.get("loc", ())
                    errors.append({
                        "loc": [str(part) for part in location if isinstance(part, (str, int))],
                        "msg": str(error.get("msg") or "Invalid value"),
                        "type": str(error.get("type") or "validation_error"),
                    })
                return _structured_error_response(
                    status_code=422,
                    message="ورودی درخواست معتبر نیست.",
                    detail=errors,
                    headers={"X-Correlation-ID": correlation_id},
                    code="AUTONOMOUS_ENGINEERING_VALIDATION_FAILED",
                    retryable=False,
                    safe_details={"validation_errors": errors},
                )
            except Exception as exc:
                _STATUS_LOGGER.error(
                    "Autonomous Engineering API failed; exception_type=%s correlation_id=%s",
                    type(exc).__name__,
                    correlation_id,
                )
                return _structured_error_response(
                    status_code=500,
                    message="خطای داخلی سرویس رخ داد؛ شناسهٔ پیگیری را برای بررسی ارائه کنید.",
                    detail="خطای داخلی سرویس رخ داد.",
                    headers={"X-Correlation-ID": correlation_id},
                    code="AUTONOMOUS_ENGINEERING_INTERNAL_ERROR",
                    retryable=False,
                    safe_details={"exception_type": type(exc).__name__},
                )
            response.headers.setdefault("X-Correlation-ID", correlation_id)
            return response

        return structured_handler
