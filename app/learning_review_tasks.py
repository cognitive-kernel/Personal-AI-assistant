"""Durable task handlers for web, peer-AI, and controlled sandbox review of lesson candidates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import learning_review_channels as channels, sandbox, sandbox_consent
from .task_manager import create_task
from .task_worker import PermanentTaskError, RetryableTaskError, TaskContext

LEARNING_REVIEW_TASK_TYPE = "learning.review_candidate"
_ALLOWED_KEYS = {"candidate_id", "query", "web_mode"}


def enqueue_candidate_review(
    candidate_id: int, *, content_sha256: str, query: str, web_mode: str = "search",
    request_id: str,
) -> dict[str, Any]:
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(content_sha256, str) or len(content_sha256) != 64:
        raise ValueError("content_sha256 must be a SHA-256 hex digest.")
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("query must contain 1–1000 characters.")
    if web_mode not in {"search", "disabled"}:
        raise ValueError("web_mode must be search or disabled.")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
        raise ValueError("request_id is required and must be at most 100 characters.")
    payload = {
        "candidate_id": candidate_id,
        "query": query.strip(),
        "web_mode": web_mode,
    }
    dedupe_key = f"learning-candidate-review:{candidate_id}:{content_sha256}:{request_id.strip()}"
    return create_task(
        LEARNING_REVIEW_TASK_TYPE,
        payload,
        priority=0,
        max_attempts=2,
        dedupe_key=dedupe_key,
    )


def handle_candidate_review(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - _ALLOWED_KEYS:
        raise PermanentTaskError("Candidate review task payload contains unsupported fields.")
    candidate_id = payload.get("candidate_id")
    query = payload.get("query")
    web_mode = payload.get("web_mode", "search")
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise PermanentTaskError("candidate_id must be a positive integer.")
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise PermanentTaskError("query must contain 1–1000 characters.")
    if web_mode not in {"search", "disabled"}:
        raise PermanentTaskError("web_mode must be search or disabled.")

    try:
        with channels.learning_connection() as connection:
            row = connection.execute(
                "SELECT content, content_sha256, topic, status FROM temporary_knowledge_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise PermanentTaskError("Learning candidate not found.")
        content_hash = hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
        if content_hash != str(row["content_sha256"]):
            raise PermanentTaskError("Candidate content hash mismatch; review is blocked.")
        if row["status"] != "awaiting_test":
            raise PermanentTaskError("Only temporary candidates awaiting tests can be reviewed.")
    except PermanentTaskError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise RetryableTaskError("Candidate storage is temporarily unavailable.") from exc

    context.checkpoint({
        "phase": "candidate_review_started",
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
        "web_mode": web_mode,
    })
    results: dict[str, Any] = {}
    if web_mode == "disabled":
        try:
            results["web_research"] = channels.record_review_attempt(
                candidate_id,
                channel="web_research",
                status="unconfigured",
                details={"reason": "disabled_by_local_user"},
            )
        except Exception as exc:
            raise RetryableTaskError("Could not record disabled web-review state.") from exc
    else:
        try:
            results["web_research"] = channels.research_candidate_online(
                candidate_id, query.strip(), limit=3
            )
        except Exception as exc:
            try:
                channels.record_review_attempt(
                    candidate_id, channel="web_research", status="failed",
                    details={"error_type": type(exc).__name__},
                )
            except Exception as db_exc:
                raise RetryableTaskError("Could not persist web-review failure.") from db_exc
            results["web_research"] = {"status": "failed", "error_type": type(exc).__name__}

    context.checkpoint({
        "phase": "candidate_review_peer_ai",
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
    })
    try:
        results["peer_ai"] = channels.consult_configured_peer_ais(candidate_id, limit=3)
    except Exception as exc:
        try:
            results["peer_ai"] = channels.record_review_attempt(
                candidate_id, channel="peer_ai", status="failed",
                details={"error_type": type(exc).__name__},
            )
            results["peer_ai"]["status"] = "failed"
        except Exception as db_exc:
            raise RetryableTaskError("Could not persist peer-review failure.") from db_exc

    context.checkpoint({
        "phase": "candidate_review_completed",
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
        "web_status": str(results.get("web_research", {}).get("status", "unknown")),
        "peer_status": str(results.get("peer_ai", {}).get("status", "unknown")),
    })
    return {
        "candidate_id": candidate_id,
        "content_sha256": content_hash,
        "channels": results,
        "promoted": False,
        "note": "Web pages and peer responses are review material only; no trust or candidate status was promoted.",
    }


LEARNING_SANDBOX_REVIEW_TASK_TYPE = "learning.sandbox_candidate"
_ALLOWED_SANDBOX_REVIEW_KEYS = {"candidate_id", "content_sha256", "test_plan_sha256", "request_id"}


def enqueue_candidate_sandbox_review(
    candidate_id: int, *, content_sha256: str, test_plan_sha256: str, request_id: str
) -> dict[str, Any]:
    """Queue an explicitly consented, bounded run of human-reviewed candidate tests."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(content_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None:
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest.")
    if not isinstance(test_plan_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", test_plan_sha256) is None:
        raise ValueError("test_plan_sha256 must be a lowercase SHA-256 digest.")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
        raise ValueError("request_id is required and must be at most 100 characters.")
    return create_task(
        LEARNING_SANDBOX_REVIEW_TASK_TYPE,
        {"candidate_id": candidate_id, "content_sha256": content_sha256,
         "test_plan_sha256": test_plan_sha256, "request_id": request_id.strip()},
        priority=0,
        max_attempts=1,
        dedupe_key=f"learning-sandbox-review:{candidate_id}:{content_sha256}:{test_plan_sha256}:{request_id.strip()}",
    )


def handle_candidate_sandbox_review(
    payload: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    context: TaskContext,
) -> dict[str, Any]:
    """Execute versioned human-reviewed tests and compare stdout without promoting knowledge."""
    if not isinstance(payload, dict) or set(payload) != _ALLOWED_SANDBOX_REVIEW_KEYS:
        raise PermanentTaskError("Sandbox review payload contains unsupported or missing fields.")
    candidate_id = payload.get("candidate_id")
    expected_hash = payload.get("content_sha256")
    expected_plan_hash = payload.get("test_plan_sha256")
    request_id = payload.get("request_id")
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise PermanentTaskError("candidate_id must be a positive integer.")
    if not isinstance(expected_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
        raise PermanentTaskError("content_sha256 must be a lowercase SHA-256 digest.")
    if not isinstance(expected_plan_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_plan_hash) is None:
        raise PermanentTaskError("test_plan_sha256 is invalid.")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 100:
        raise PermanentTaskError("request_id is invalid.")

    try:
        with channels.learning_connection() as connection:
            row = connection.execute(
                """SELECT content, content_sha256, status, test_plan_json
                   FROM temporary_knowledge_candidates WHERE id=?""",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise PermanentTaskError("Learning candidate not found.")
        actual_hash = hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
        if actual_hash != expected_hash or actual_hash != str(row["content_sha256"]):
            raise PermanentTaskError("Candidate content hash mismatch; sandbox execution is blocked.")
        if row["status"] != "awaiting_test":
            raise PermanentTaskError("Only temporary candidates awaiting tests can be sandbox-reviewed.")
        try:
            plan = json.loads(row["test_plan_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PermanentTaskError("Candidate test plan is invalid.") from exc
        canonical_plan = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        actual_plan_hash = hashlib.sha256(canonical_plan.encode("utf-8")).hexdigest()
        if actual_plan_hash != expected_plan_hash:
            raise PermanentTaskError("Candidate test plan changed after sandbox review was queued.")
        if (
            not isinstance(plan, dict)
            or plan.get("version") != 2
            or plan.get("oracle_reviewed") is not True
            or not isinstance(plan.get("tests"), list)
            or not 1 <= len(plan["tests"]) <= 3
        ):
            raise PermanentTaskError("A human-reviewed plan with 1–3 executable tests is required before sandbox execution.")
        for test in plan["tests"]:
            if (
                not isinstance(test, dict)
                or not isinstance(test.get("name"), str)
                or not test["name"].strip()
                or not isinstance(test.get("python_test_code"), str)
                or not test["python_test_code"].strip()
                or "\x00" in test["python_test_code"]
                or len(test["python_test_code"].encode("utf-8")) > 8192
                or not isinstance(test.get("expected_stdout"), str)
                or len(test["expected_stdout"]) > 1000
            ):
                raise PermanentTaskError("Every sandbox test must include bounded Python code and an expected stdout string.")
    except PermanentTaskError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise RetryableTaskError("Candidate storage is temporarily unavailable.") from exc

    if not sandbox_consent.is_enabled():
        try:
            channels.record_review_attempt(candidate_id, channel="sandbox", status="sandbox_unconfigured",
                                            details={"reason": "explicit_sandbox_consent_missing", "request_id": request_id})
        except Exception as exc:
            raise RetryableTaskError("Could not persist sandbox consent state.") from exc
        return {"candidate_id": candidate_id, "status": "sandbox_unconfigured", "executed": 0, "promoted": False}

    enabled = os.getenv("SANDBOX_ENABLED", "").strip().lower()
    executable_value = os.getenv("SANDBOX_DOCKER_EXECUTABLE", "").strip()
    image = os.getenv("SANDBOX_PYTHON_IMAGE", "").strip()
    if enabled not in {"1", "true", "yes", "on"} or not executable_value:
        channels.record_review_attempt(candidate_id, channel="sandbox", status="sandbox_unconfigured",
                                        details={"reason": "sandbox_runtime_not_configured", "request_id": request_id})
        return {"candidate_id": candidate_id, "status": "sandbox_unconfigured", "executed": 0, "promoted": False}
    executable = Path(executable_value).expanduser()
    if not executable.is_absolute() or not executable.is_file() or re.fullmatch(
        r"(?:python|docker\.io/library/python):[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}", image
    ) is None:
        channels.record_review_attempt(candidate_id, channel="sandbox", status="sandbox_unconfigured",
                                        details={"reason": "invalid_docker_path_or_image_digest", "request_id": request_id})
        return {"candidate_id": candidate_id, "status": "sandbox_unconfigured", "executed": 0, "promoted": False}

    outcomes: list[dict[str, Any]] = []
    for index, test in enumerate(plan["tests"], start=1):
        source = test["python_test_code"].strip()
        expected_stdout = test["expected_stdout"]
        if not sandbox_consent.is_enabled():
            outcomes.append({"test": test["name"], "status": "cancelled", "reason": "consent_revoked"})
            break
        context.checkpoint({"phase": "candidate_sandbox_test", "candidate_id": candidate_id,
                            "content_sha256": actual_hash, "test_plan_sha256": actual_plan_hash, "test": test["name"],
                            "index": index, "total": len(plan["tests"])})
        def control_check() -> str:
            status = context.control_status()
            if status != "running":
                return status
            return "running" if sandbox_consent.is_enabled() else "control_unavailable"
        result = sandbox.run_python(
            source, docker_executable=executable, image=image, timeout_seconds=10,
            max_output_bytes=8192, control_check=control_check, task_id=context.task_id,
        )
        passed = result.status == "succeeded" and result.stdout == expected_stdout
        outcomes.append({
            "test": test["name"],
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "expected_stdout_sha256": hashlib.sha256(expected_stdout.encode("utf-8")).hexdigest(),
            "actual_stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
            "expected_stdout": expected_stdout[:300],
            "actual_stdout": result.stdout[:300],
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "passed": passed,
            "reason": (result.reason or "")[:200],
        })
        if result.status == "unavailable":
            break

    successful = bool(outcomes) and len(outcomes) == len(plan["tests"]) and all(item.get("passed") is True for item in outcomes)
    terminal_status = (
        "unavailable" if any(item.get("status") == "unavailable" for item in outcomes)
        else "completed" if successful else "failed"
    )
    executed_count = sum(
        item.get("status") in {"succeeded", "failed", "timed_out", "paused", "cancelled", "output_limit_exceeded"}
        for item in outcomes
    )
    channels.record_review_attempt(
        candidate_id, channel="sandbox", status=terminal_status,
        details={"request_id": request_id, "content_sha256": actual_hash, "test_plan_sha256": actual_plan_hash, "image": image,
                 "executed": executed_count, "passed": sum(item.get("passed") is True for item in outcomes),
                 "outcomes": outcomes,
                 "interpretation": "Human-attested expected stdout was compared exactly; this is bounded evidence, not universal truth and never promotes the candidate."},
    )
    return {
        "candidate_id": candidate_id, "content_sha256": actual_hash, "test_plan_sha256": actual_plan_hash,
        "status": terminal_status, "executed": executed_count, "outcomes": outcomes,
        "promoted": False,
        "note": "Sandbox tests compare exact stdout against a human-attested expectation; results never promote candidate knowledge.",
    }
