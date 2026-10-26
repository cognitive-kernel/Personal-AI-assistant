"""Controlled runner that connects sandbox execution to versioned experience evidence."""
from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from . import experience, sandbox_consent, sandbox_tasks, web_sources
from .task_worker import TaskContext

EXPERIENCE_TEST_TASK_TYPE = "experience.run_test"
_OFFICIAL_SOURCE_HOSTS = (
    "docs.python.org", "learn.microsoft.com", "dev.mysql.com", "sqlite.org",
    "doc.rust-lang.org", "rust-lang.org", "help.mikrotik.com", "mikrotik.com",
    "docs.vmware.com", "knowledge.broadcom.com", "developer.android.com",
    "developer.apple.com", "developer.mozilla.org", "docs.docker.com",
    "docs.kernel.org", "cisco.com", "metatrader5.com", "metatrader4.com",
)
_ALLOWED_PAYLOAD_KEYS = {
    "experience_id",
    "test_name",
    "source_code",
    "timeout_seconds",
    "max_output_bytes",
}


def validate_test_harness(source_code: str) -> None:
    """Require a bounded, syntactically valid Python harness with at least one assert."""
    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("source_code must be non-empty text.")
    if "\x00" in source_code or len(source_code.encode("utf-8")) > 64 * 1024:
        raise ValueError("source_code exceeds the 64 KiB limit or contains NUL characters.")
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise ValueError("source_code must be valid Python.") from exc
    if not any(isinstance(node, ast.Assert) for node in ast.walk(tree)):
        raise ValueError("Test harness must contain at least one Python assert statement.")


def _verify_live_source_evidence(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Require exact excerpts from current official pages before sandbox execution."""
    evidence = item.get("source_evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
        raise ValueError("Experience needs one to three source evidence excerpts before a test can run.")
    verified: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for entry in evidence:
        if not isinstance(entry, dict):
            raise ValueError("Every source evidence entry must be an object.")
        url, claim, excerpt = entry.get("url"), entry.get("claim"), entry.get("excerpt")
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError("Every source evidence entry needs a bounded HTTPS URL.")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Experience source URL has an invalid port.") from exc
        if (parsed.scheme.lower() != "https" or not host or parsed.username is not None
                or parsed.password is not None or port not in (None, 443)
                or not any(host == allowed or host.endswith("." + allowed) for allowed in _OFFICIAL_SOURCE_HOSTS)):
            raise ValueError("Experience verification requires an allow-listed official HTTPS source.")
        if url in seen_urls:
            raise ValueError("Duplicate source URLs are not accepted in one verification run.")
        seen_urls.add(url)
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 300:
            raise ValueError("Every source evidence entry needs a concise supported-claim description.")
        if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 1000:
            raise ValueError("Every source evidence entry needs an exact excerpt of 1–1000 characters.")
        try:
            document = web_sources.fetch_public_document(url, "experience source evidence", max_chars=100000)
        except Exception as exc:
            raise ValueError("A source could not be safely fetched live; verification fails closed (" + type(exc).__name__ + ").") from exc
        normalize = lambda value: re.sub(r"\s+", " ", value).strip()
        normalized_excerpt = normalize(excerpt)
        if normalized_excerpt not in normalize(document.text):
            raise ValueError("A saved evidence excerpt is absent from the current live source page.")
        verified.append({
            **entry,
            "live_content_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
            "excerpt_sha256": hashlib.sha256(normalized_excerpt.encode("utf-8")).hexdigest(),
            "live_verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return verified


def _validate_experience_test(item: dict[str, Any], test_name: str) -> None:
    if item.get("status") == "verified":
        raise ValueError("Verified experiences are immutable; create a new experience to retest.")
    evidence = item.get("source_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Experience needs source evidence before a test can run.")
    plan = item.get("test_plan")
    if not isinstance(plan, list) or not plan:
        raise ValueError("Experience needs a test plan before a test can run.")
    if not any(
        isinstance(entry, dict)
        and isinstance(entry.get("test"), str)
        and entry["test"].strip() == test_name
        for entry in plan
    ):
        raise ValueError("test_name must exactly match an item in the saved test plan.")


def handler_from_environment() -> Callable[
    [dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]
] | None:
    """Expose the experience runner only when the administrator-configured sandbox exists."""
    sandbox_handler = sandbox_tasks.handler_from_environment()
    if sandbox_handler is None:
        return None

    def handle(
        payload: dict[str, Any],
        checkpoint: dict[str, Any] | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - _ALLOWED_PAYLOAD_KEYS:
            raise ValueError("Experience test payload contains unsupported fields.")
        experience_id = payload.get("experience_id")
        if isinstance(experience_id, bool) or not isinstance(experience_id, int) or experience_id < 1:
            raise ValueError("experience_id must be a positive integer.")
        test_name = payload.get("test_name")
        if not isinstance(test_name, str) or not test_name.strip() or len(test_name) > 300:
            raise ValueError("test_name must contain 1–300 characters.")
        source_code = payload.get("source_code")
        validate_test_harness(source_code)
        timeout_seconds = payload.get("timeout_seconds", 10)
        max_output_bytes = payload.get("max_output_bytes", 32 * 1024)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be an integer from 1 to 120.")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536:
            raise ValueError("max_output_bytes must be an integer from 1 to 65536.")
        if not sandbox_consent.is_enabled():
            raise ValueError("Sandbox execution requires explicit, current user consent.")

        item = experience.get_experience(experience_id)
        _validate_experience_test(item, test_name)
        verified_evidence = _verify_live_source_evidence(item)
        context.checkpoint({
            "phase": "experience_test_started",
            "experience_id": experience_id,
            "test_name": test_name,
        })
        result = sandbox_handler(
            {
                "source_code": source_code,
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
            },
            checkpoint,
            context,
        )
        if context.control_status() != "running":
            raise RuntimeError("Experience test was stopped before its evidence could be recorded.")
        if not sandbox_consent.is_enabled():
            raise RuntimeError("Experience test was stopped because sandbox consent was revoked.")
        passed = result.get("execution_status") == "succeeded" and result.get("exit_code") == 0
        actual = (
            "stdout:\n" + str(result.get("stdout", ""))[:2000]
            + "\nstderr:\n" + str(result.get("stderr", ""))[:1000]
        )[:4000]
        test_result = {
            "test": test_name,
            "passed": bool(passed),
            "actual": actual,
            "runner": "docker-sandbox",
            "source_sha256": str(result.get("source_sha256", "")),
            "execution_status": str(result.get("execution_status", "unknown")),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "image": str(result.get("image", "")),
        }
        test_result["source_evidence"] = [
            {"url": evidence["url"], "live_content_sha256": evidence["live_content_sha256"], "excerpt_sha256": evidence["excerpt_sha256"]}
            for evidence in verified_evidence
        ]
        all_results = list(item.get("test_results") or []) + [test_result]
        updated = experience.update_experience(
            experience_id,
            status="verified" if passed else "failed",
            source_evidence=verified_evidence,
            test_plan=item["test_plan"],
            test_results=all_results,
            takeaway=item.get("takeaway", ""),
            _controlled_run=True,
        )
        context.checkpoint({
            "phase": "experience_test_recorded",
            "experience_id": experience_id,
            "test_name": test_name,
            "passed": bool(passed),
            "experience_version": updated["version"],
            "experience_status": updated["status"],
        })
        return {
            "experience_id": experience_id,
            "test_name": test_name,
            "passed": bool(passed),
            "experience_version": updated["version"],
            "experience_status": updated["status"],
            "test_result": test_result,
        }

    return handle
