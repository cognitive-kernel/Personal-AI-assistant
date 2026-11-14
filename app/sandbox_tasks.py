"""Allow-listed task adapter for isolated Python sandbox execution.

The module switch defaults to enabled in Settings, but handler registration
requires valid local Docker configuration. Every execution separately requires
explicit, persisted and revocable user consent; payloads cannot override setup.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import db, sandbox, sandbox_consent, task_manager
from .task_worker import PermanentTaskError, TaskContext

logger = logging.getLogger("personal_ai_assistant.sandbox_tasks")

SANDBOX_TASK_TYPE = "sandbox.run_python"
SANDBOX_LANGUAGE_TASK_TYPE = "sandbox.run_language"
_IMAGE_REF = re.compile(
    r"^(?:python|docker\.io/library/python):[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$"
)
_ALLOWED_PAYLOAD_KEYS = {"source_code", "timeout_seconds", "max_output_bytes", "learning_practical"}


def validate_settings(settings: dict[str, Any]) -> None:
    """Validate persisted sandbox configuration without granting execution consent."""
    if not isinstance(settings, dict):
        raise ValueError("Sandbox settings must be a mapping.")
    enabled = settings.get("sandbox_enabled", True)
    if type(enabled) is not bool:
        raise ValueError("sandbox_enabled must be a boolean setting.")
    executable_value = str(settings.get("sandbox_docker_executable", "") or "").strip()
    if executable_value and not Path(executable_value).expanduser().is_absolute():
        raise ValueError("sandbox_docker_executable must be an absolute file path.")
    python_image = str(settings.get("sandbox_python_image", "") or "").strip()
    if python_image and not _IMAGE_REF.fullmatch(python_image):
        raise ValueError("sandbox_python_image must be an official Python image pinned by SHA-256 digest.")
    test_image = str(settings.get("sandbox_test_image", "") or "").strip()
    if test_image and not sandbox.is_valid_test_image_reference(test_image):
        raise ValueError("sandbox_test_image must be an immutable sha256 image ID or digest-pinned repository reference.")
    node_image = str(settings.get("sandbox_node_image", "") or "").strip()
    if node_image and not sandbox.is_valid_language_image_reference(node_image, "javascript"):
        raise ValueError("sandbox_node_image must be an official Node image pinned by SHA-256 digest.")
    rust_image = str(settings.get("sandbox_rust_image", "") or "").strip()
    if rust_image and not sandbox.is_valid_language_image_reference(rust_image, "rust"):
        raise ValueError("sandbox_rust_image must be an official Rust image pinned by SHA-256 digest.")


def _unavailable_handler(reason: str) -> Callable[
    [dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]
]:
    def handle(
        _payload: dict[str, Any],
        _checkpoint: dict[str, Any] | None,
        _context: TaskContext,
    ) -> dict[str, Any]:
        raise PermanentTaskError(reason)

    return handle


def handler_from_environment(*, allow_language_runners: bool = False) -> Callable[
    [dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]
]:
    """Always register the task type; invalid or disabled setup fails closed at execution."""
    try:
        settings = db.get_settings()
        validate_settings(settings)
    except Exception as exc:
        return _unavailable_handler(
            f"Sandbox configuration is unavailable: {type(exc).__name__}: {exc}"
        )
    if settings.get("sandbox_enabled", True) is not True:
        return _unavailable_handler("Sandbox module is disabled in settings.")

    executable_value = str(settings.get("sandbox_docker_executable", "") or "").strip()
    image = str(settings.get("sandbox_python_image", "") or "").strip()
    if not executable_value or not image:
        return _unavailable_handler(
            "Sandbox is enabled but Docker executable or Python image is not configured."
        )
    executable = Path(executable_value).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        return _unavailable_handler("sandbox_docker_executable must be an existing absolute file path.")
    if not _IMAGE_REF.fullmatch(image):
        return _unavailable_handler("sandbox_python_image must be an official Python image pinned by SHA-256 digest.")
    node_image = str(settings.get("sandbox_node_image", "") or "").strip()
    rust_image = str(settings.get("sandbox_rust_image", "") or "").strip()
    if allow_language_runners:
        if node_image and not sandbox.is_valid_language_image_reference(node_image, "javascript"):
            return _unavailable_handler("sandbox_node_image must be an official Node image pinned by SHA-256 digest.")
        if rust_image and not sandbox.is_valid_language_image_reference(rust_image, "rust"):
            return _unavailable_handler("sandbox_rust_image must be an official Rust image pinned by SHA-256 digest.")

    def handle(
        payload: dict[str, Any],
        checkpoint: dict[str, Any] | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Sandbox task payload must be a JSON object.")
        allowed_keys = _ALLOWED_PAYLOAD_KEYS | ({"language"} if allow_language_runners else set())
        if set(payload) - allowed_keys:
            raise ValueError("Sandbox task payload contains unsupported fields.")
        language = payload.get("language", "python")
        if not isinstance(language, str) or language not in ({"python", "sqlite", "javascript", "rust"} if allow_language_runners else {"python"}):
            raise ValueError("Unsupported sandbox language.")
        selected_image = image
        if language == "javascript":
            selected_image = node_image
            if not selected_image:
                raise ValueError("SANDBOX_NODE_IMAGE is required for JavaScript factual tests.")
        elif language == "rust":
            selected_image = rust_image
            if not selected_image:
                raise ValueError("SANDBOX_RUST_IMAGE is required for Rust factual tests.")
        source_code = payload.get("source_code")
        if not isinstance(source_code, str) or not source_code.strip():
            raise ValueError("source_code must be non-empty text.")
        if len(source_code.encode("utf-8")) > 64 * 1024 or "\x00" in source_code:
            raise ValueError("source_code exceeds the allowed size or contains NUL characters.")

        practical_metadata = payload.get("learning_practical")
        learner_source_code = source_code
        if practical_metadata is not None:
            if language != "python":
                raise ValueError("Practical learning assessment supports Python only.")
            if (
                not isinstance(practical_metadata, dict)
                or set(practical_metadata) != {"exercise_id", "job_id"}
                or not isinstance(practical_metadata.get("exercise_id"), str)
                or isinstance(practical_metadata.get("job_id"), bool)
                or not isinstance(practical_metadata.get("job_id"), int)
                or practical_metadata["job_id"] < 1
            ):
                raise ValueError("Learning practical metadata is invalid.")
            from .learning_practical import build_grader_source
            source_code = build_grader_source(practical_metadata["exercise_id"], learner_source_code)
            if len(source_code.encode("utf-8")) > 64 * 1024:
                raise ValueError("Generated practical grader exceeds the sandbox source limit.")

        timeout_seconds = payload.get("timeout_seconds", settings.get("sandbox_timeout_seconds", 60))
        max_output_bytes = payload.get("max_output_bytes", settings.get("sandbox_max_output_bytes", 32768))
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be an integer from 1 to 120.")
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= 65536
        ):
            raise ValueError("max_output_bytes must be an integer from 1 to 65536.")

        if not sandbox_consent.is_enabled():
            raise ValueError("Sandbox execution requires explicit, current user consent.")

        source_hash = hashlib.sha256(learner_source_code.encode("utf-8")).hexdigest()
        context.checkpoint(
            {
                "phase": "sandbox_started",
                "source_sha256": source_hash,
                "image": selected_image,
                "language": language,
            }
        )
        last_consent_check = 0.0
        consent_revoked = False

        def control_check() -> str:
            nonlocal last_consent_check, consent_revoked
            status = context.control_status()
            if status != "running":
                return status
            now = time.monotonic()
            if now - last_consent_check >= 0.5:
                last_consent_check = now
                if not sandbox_consent.is_enabled():
                    consent_revoked = True
                    return "control_unavailable"
            return "running"

        practical_assessment = None
        practical_completed = False
        practical_case_count = 0
        if practical_metadata is not None:
            import json
            from .learning_practical import (
                build_grade_report, get_test_cases, grade_case_output, record_practical_attempt,
            )
            case_results = []
            total_duration_ms = 0
            result = None
            cases = get_test_cases(practical_metadata["exercise_id"])
            practical_case_count = len(cases)
            for case in cases:
                preflight_status = control_check()
                if preflight_status != "running":
                    stopped_status = "paused" if preflight_status in {"pause_requested", "paused"} else "cancelled"
                    result = sandbox.SandboxResult(
                        stopped_status, None, "", "", total_duration_ms,
                        "Practical grading stopped before all test cases completed.",
                    )
                    break
                input_literal = json.dumps(
                    json.dumps(case["input"], ensure_ascii=False), ensure_ascii=True
                )
                source_literal = json.dumps(learner_source_code, ensure_ascii=True)
                case_source = (
                    "import io,json,sys\\n"
                    f"sys.stdin=io.TextIOWrapper(io.BytesIO(json.loads({input_literal}).encode('utf-8')))\\n"
                    f"exec(compile(json.loads({source_literal}), '<learner>', 'exec'), {{'__name__':'__main__'}})\\n"
                )
                case_result = sandbox.run_python(
                    case_source,
                    docker_executable=executable,
                    image=selected_image,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                    control_check=control_check,
                    task_id=context.task_id,
                )
                total_duration_ms += case_result.duration_ms
                if case_result.status in {"unavailable", "paused", "cancelled"}:
                    result = sandbox.SandboxResult(
                        case_result.status, case_result.exit_code, "", "", total_duration_ms,
                        case_result.reason,
                    )
                    break
                case_results.append({
                    "case_id": case["id"],
                    "passed": grade_case_output(
                        practical_metadata["exercise_id"], case["id"],
                        case_result.status, case_result.exit_code, case_result.stdout,
                    ),
                })
            if result is None:
                grading_report = build_grade_report(practical_metadata["exercise_id"], case_results)
                result = sandbox.SandboxResult(
                    "succeeded", 0, json.dumps(grading_report, separators=(",", ":")),
                    "", total_duration_ms,
                )
                practical_completed = True
        elif language in {"javascript", "rust"}:
            result = sandbox.run_language(
                source_code,
                language=language,
                docker_executable=executable,
                image=selected_image,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                control_check=control_check,
                task_id=context.task_id,
            )
        else:
            result = sandbox.run_python(
                source_code,
                docker_executable=executable,
                image=selected_image,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                control_check=control_check,
                task_id=context.task_id,
            )
        evidence = {
            "phase": "sandbox_finished",
            "source_sha256": source_hash,
            "image": selected_image,
            "language": language,
            "execution_status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            **({"practical_case_count": practical_case_count, "practical_completed": practical_completed}
               if practical_metadata is not None else {}),
        }
        context.checkpoint(evidence)
        if consent_revoked:
            raise RuntimeError("Sandbox execution stopped because user consent was revoked.")
        if practical_metadata is not None and practical_completed:
            try:
                grading_report = json.loads(result.stdout)
                practical_assessment = record_practical_attempt(
                    practical_metadata["job_id"],
                    practical_metadata["exercise_id"],
                    grading_report,
                    learner_source_code,
                    idempotency_key=f"sandbox-practical-task:{context.task_id}",
                )
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise RuntimeError("Practical grading output could not be validated or recorded.") from exc
        return {
            **evidence,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "reason": result.reason,
            **({"practical_assessment": practical_assessment} if practical_assessment is not None else {}),
        }

    return handle



def cleanup_terminal_containers() -> None:
    """Periodically clean stale containers only after the task store reports terminal IDs."""
    settings = db.get_settings()
    if settings.get("sandbox_enabled", True) is not True:
        return
    executable_value = str(settings.get("sandbox_docker_executable", "") or "").strip()
    executable = Path(executable_value).expanduser()
    if not executable_value or not executable.is_absolute() or not executable.is_file():
        logger.warning("Sandbox cleanup skipped: configured Docker executable is unavailable.")
        return

    # TaskWorker.run_once() calls claim_next_task() before runtime maintenance, so
    # expired leases are reconciled by the queue before terminal IDs are collected.
    terminal_task_ids = set(
        task_manager.list_terminal_task_ids(limit=1000, min_terminal_age_seconds=60)
    )
    result = sandbox.cleanup_orphaned_containers(
        docker_executable=executable,
        terminal_task_ids=terminal_task_ids,
        min_age_seconds=60,
    )
    if result.removed:
        logger.info("Removed %d stale sandbox container(s).", len(result.removed))
    if result.errors:
        logger.warning("Sandbox cleanup reported %d error(s): %s", len(result.errors), "; ".join(result.errors))
