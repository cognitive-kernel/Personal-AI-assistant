"""Allow-listed task adapter for isolated Python sandbox execution.

The handler is disabled by default. Its Docker executable and image are
administrator-configured environment values; task payloads cannot override them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from . import sandbox, sandbox_consent, task_manager
from .task_worker import TaskContext

logger = logging.getLogger("personal_ai_assistant.sandbox_tasks")

SANDBOX_TASK_TYPE = "sandbox.run_python"
_IMAGE_REF = re.compile(
    r"^(?:python|docker\.io/library/python):[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$"
)
_ALLOWED_PAYLOAD_KEYS = {"source_code", "timeout_seconds", "max_output_bytes", "learning_practical"}


def handler_from_environment() -> Callable[
    [dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]
] | None:
    """Return a handler only after explicit opt-in and valid local configuration."""
    enabled = os.getenv("SANDBOX_ENABLED", "").strip().lower()
    if enabled in {"", "0", "false", "no", "off"}:
        return None
    if enabled not in {"1", "true", "yes", "on"}:
        raise ValueError("SANDBOX_ENABLED must be a boolean value.")

    executable_value = os.getenv("SANDBOX_DOCKER_EXECUTABLE", "").strip()
    image = os.getenv("SANDBOX_PYTHON_IMAGE", "").strip()
    if not executable_value:
        raise ValueError("SANDBOX_DOCKER_EXECUTABLE is required when sandbox is enabled.")
    executable = Path(executable_value).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("SANDBOX_DOCKER_EXECUTABLE must be an existing absolute file path.")
    if not _IMAGE_REF.fullmatch(image):
        raise ValueError("SANDBOX_PYTHON_IMAGE must be an official Python image pinned by SHA-256 digest.")

    def handle(
        payload: dict[str, Any],
        checkpoint: dict[str, Any] | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("Sandbox task payload must be a JSON object.")
        if set(payload) - _ALLOWED_PAYLOAD_KEYS:
            raise ValueError("Sandbox task payload contains unsupported fields.")
        source_code = payload.get("source_code")
        if not isinstance(source_code, str) or not source_code.strip():
            raise ValueError("source_code must be non-empty text.")
        if len(source_code.encode("utf-8")) > 64 * 1024 or "\x00" in source_code:
            raise ValueError("source_code exceeds the allowed size or contains NUL characters.")

        practical_metadata = payload.get("learning_practical")
        learner_source_code = source_code
        if practical_metadata is not None:
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

        timeout_seconds = payload.get("timeout_seconds", 10)
        max_output_bytes = payload.get("max_output_bytes", 32 * 1024)
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
                "image": image,
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

        result = sandbox.run_python(
            source_code,
            docker_executable=executable,
            image=image,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            control_check=control_check,
            task_id=context.task_id,
        )
        evidence = {
            "phase": "sandbox_finished",
            "source_sha256": source_hash,
            "image": image,
            "execution_status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
        }
        context.checkpoint(evidence)
        if consent_revoked:
            raise RuntimeError("Sandbox execution stopped because user consent was revoked.")
        practical_assessment = None
        if practical_metadata is not None and result.status == "completed" and result.exit_code == 0:
            try:
                grading_report = json.loads(result.stdout)
                from .learning_practical import record_practical_attempt

                practical_assessment = record_practical_attempt(
                    practical_metadata["job_id"],
                    practical_metadata["exercise_id"],
                    grading_report,
                    learner_source_code,
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
    enabled = os.getenv("SANDBOX_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    executable_value = os.getenv("SANDBOX_DOCKER_EXECUTABLE", "").strip()
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
