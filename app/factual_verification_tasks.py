"""Consent-gated Docker verification for sourced Python and SQLite factual claims."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
from typing import Any, Callable
from urllib.parse import urlparse

from . import factual_knowledge, factual_semantic_review, sandbox_consent, sandbox_tasks
from .db import learning_connection
from .task_worker import TaskContext

FACTUAL_VERIFY_TASK_TYPE = "factual.verify_claim"
_ALLOWED_PAYLOAD_KEYS = {
    "knowledge_id", "test_name", "source_code", "timeout_seconds", "max_output_bytes",
    "oracle_reviewed", "oracle_review_note", "oracle_review_id",
}
_RUNTIME_MARKER = re.compile(r"^__PAI_PYTHON_VERSION__:(\d+\.\d+\.\d+)$")
_SQLITE_RUNTIME_MARKER = re.compile(r"^__PAI_SQLITE_VERSION__:(\d+\.\d+\.\d+)$")
_NODE_RUNTIME_MARKER = re.compile(r"^__PAI_NODE_VERSION__:(\d+\.\d+\.\d+)$")
_RUST_RUNTIME_MARKER = re.compile(r"^__PAI_RUST_VERSION__:(\d+\.\d+\.\d+)$")
_OFFICIAL_SOURCE_HOSTS = (
    "docs.python.org", "learn.microsoft.com", "dev.mysql.com", "sqlite.org",
    "doc.rust-lang.org", "rust-lang.org", "nodejs.org", "help.mikrotik.com", "mikrotik.com",
    "docs.vmware.com", "knowledge.broadcom.com", "developer.android.com",
    "developer.apple.com", "developer.mozilla.org", "docs.docker.com",
    "docs.kernel.org", "cisco.com", "metatrader5.com", "metatrader4.com",
)


def _verify_live_source_excerpt(source_url: str, stored_excerpt: str) -> str:
    """Verify that a bounded stored quotation still exists on its allow-listed HTTPS source."""
    if not isinstance(source_url, str) or len(source_url) > 1500:
        raise ValueError("Live source URL is invalid or too long.")
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or not any(host == allowed or host.endswith("." + allowed) for allowed in _OFFICIAL_SOURCE_HOSTS)
    ):
        raise ValueError("Live source verification requires an allow-listed official HTTPS documentation host.")
    if not isinstance(stored_excerpt, str) or not stored_excerpt.strip() or len(stored_excerpt) > 1000:
        raise ValueError("Stored source excerpt is invalid or exceeds the verification limit.")
    from . import web_sources

    try:
        document = web_sources.fetch_public_document(
            source_url, "factual claim evidence", max_chars=100000
        )
    except Exception as exc:
        raise ValueError(
            "Live source could not be safely fetched; factual verification fails closed ("
            + type(exc).__name__ + ")."
        ) from exc
    normalize = lambda value: re.sub(r"\s+", " ", value).strip()
    excerpt_normalized = normalize(stored_excerpt)
    document_normalized = normalize(document.text)
    if excerpt_normalized not in document_normalized:
        raise ValueError("Stored evidence excerpt is absent from the current live source page.")
    return hashlib.sha256(document.text.encode("utf-8")).hexdigest()


def validate_test_harness(source_code: str) -> None:
    """Accept bounded Python assertions only as an explicitly submitted test harness."""
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


def get_verification_candidate(knowledge_id: int) -> dict[str, Any]:
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
        raise ValueError("knowledge_id must be a positive integer.")
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT id, claim, claim_hash, evidence_level, lifecycle_status,
                      domain_name, environment_name, environment_version
               FROM factual_knowledge WHERE id=?""",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            raise KeyError("Factual claim not found.")
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM factual_evidence WHERE knowledge_id=?",
            (knowledge_id,),
        ).fetchone()[0]
    item = dict(row)
    runner_languages = {
        ("python", "python"): "python",
        ("sqlite", "sqlite"): "sqlite",
        ("javascript", "javascript"): "javascript",
        ("javascript", "node"): "javascript",
        ("node", "node"): "javascript",
        ("rust", "rust"): "rust",
    }
    runner_language = runner_languages.get((item["domain_name"], item["environment_name"]))
    if runner_language is None:
        raise ValueError("The current Docker verification runner supports Python, SQLite, JavaScript/Node, and Rust factual claims only.")
    item["runner_language"] = runner_language
    if item["evidence_level"] != "L1_sourced" or item["lifecycle_status"] != "active":
        raise ValueError("Only active L1_sourced claims can be submitted for sandbox verification.")
    if evidence_count < 1:
        raise ValueError("The claim must have traceable source evidence before verification.")
    return item


def _validate_domain_test_harness(source_code: str, runner_language: str) -> None:
    if runner_language in {"python", "sqlite"}:
        factual_knowledge.validate_test_harness(source_code)
        return
    if not isinstance(source_code, str) or not source_code.strip() or len(source_code.encode("utf-8")) > 64 * 1024 or "\x00" in source_code:
        raise ValueError("Test harness must be non-empty text no larger than 64 KiB.")
    if runner_language == "javascript":
        if not re.search(r"(?:\bassert(?:\.|\s*\()|throw\s+new\s+Error\s*\()", source_code):
            raise ValueError("JavaScript factual tests must contain an explicit assertion or throw new Error.")
        return
    if runner_language == "rust":
        if not re.search(r"\bassert(?:_eq|_ne)?!\s*\(", source_code):
            raise ValueError("Rust factual tests must contain assert!, assert_eq!, or assert_ne!.")
        if "fn main" in source_code:
            raise ValueError("Rust test input must be statements for the isolated main wrapper, not a custom program.")
        return
    raise ValueError("Unsupported factual test runner language.")


def _wrapped_harness(source_code: str, runner_language: str = "python") -> tuple[str, str]:
    completion_marker = "__PAI_TEST_COMPLETED__:" + secrets.token_hex(16)
    if runner_language in {"python", "sqlite"}:
        encoded = json.dumps(source_code, ensure_ascii=False)
        wrapper = (
            "import json as __pai_json, platform as __pai_platform\n"
            "print('__PAI_PYTHON_VERSION__:' + __pai_platform.python_version(), flush=True)\n"
            "import sqlite3 as __pai_sqlite\n"
            "print('__PAI_SQLITE_VERSION__:' + __pai_sqlite.sqlite_version, flush=True)\n"
            f"__pai_source = __pai_json.loads({json.dumps(encoded)})\n"
            "exec(compile(__pai_source, '<factual-claim-test>', 'exec'), {'__name__': '__main__'})\n"
            f"print({json.dumps(completion_marker)}, flush=True)\n"
        )
        return wrapper, completion_marker
    if runner_language == "javascript":
        wrapper = (
            "console.log('__PAI_NODE_VERSION__:' + process.versions.node);\n"
            "(async () => {\n"
            + source_code + "\n"
            + f"console.log({json.dumps(completion_marker)});\n"
            + "})().catch((error) => { console.error(error); process.exitCode = 1; });\n"
        )
        return wrapper, completion_marker
    if runner_language == "rust":
        wrapper = (
            "fn main() {\n"
            + source_code + "\n"
            + f"println!({json.dumps(completion_marker)});\n"
            + "}\n"
        )
        return wrapper, completion_marker
    raise ValueError("Unsupported factual test runner language.")


def _runtime_version(
    stdout: str, completion_marker: str, runner_language: str = "python"
) -> tuple[str | None, bool, str]:
    lines = stdout.splitlines()
    if not lines:
        return None, False, ""
    if runner_language == "javascript":
        runtime_match = _NODE_RUNTIME_MARKER.fullmatch(lines[0].strip())
    elif runner_language == "rust":
        runtime_match = _RUST_RUNTIME_MARKER.fullmatch(lines[0].strip())
    else:
        python_match = _RUNTIME_MARKER.fullmatch(lines[0].strip())
        sqlite_match = next(
            (
                match for line in lines[1:3]
                if (match := _SQLITE_RUNTIME_MARKER.fullmatch(line.strip())) is not None
            ),
            None,
        )
        runtime_match = sqlite_match if runner_language == "sqlite" else python_match
    if runtime_match is None:
        return None, False, stdout[:2000]
    completed = lines[-1].strip() == completion_marker
    output_lines = [
        line for line in (lines[1:-1] if completed else lines[1:])
        if _RUNTIME_MARKER.fullmatch(line.strip()) is None
        and _SQLITE_RUNTIME_MARKER.fullmatch(line.strip()) is None
        and _NODE_RUNTIME_MARKER.fullmatch(line.strip()) is None
        and _RUST_RUNTIME_MARKER.fullmatch(line.strip()) is None
    ]
    return runtime_match.group(1), completed, "\n".join(output_lines)[:2000]


def _bounded_summary_json(summary: dict[str, Any]) -> str:
    """Serialize a valid JSON audit summary within the database's 2 KiB field limit."""
    bounded = dict(summary)
    for key, limit in (
        ("configured_image", 120),
        ("semantic_evaluator_model", 80),
        ("semantic_review_rationale", 180),
        ("semantic_source_url", 180),
        ("oracle_source_url", 180),
        ("oracle_review_note", 120),
    ):
        value = bounded.get(key)
        if isinstance(value, str):
            bounded[key] = value[:limit]
    encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= 2000:
        return encoded
    essential_keys = (
        "runner", "execution_status", "exit_code", "runtime_version",
        "claim_hash_at_start", "test_source_sha256", "oracle_review_id",
        "semantic_rubric_version", "semantic_review_score", "semantic_review_verdict",
        "semantic_review_confidence", "semantic_evaluator_model", "semantic_evaluator_digest",
        "semantic_source_excerpt_sha256", "live_source_verified", "live_source_hashes", "output_hash",
    )
    essential = {key: bounded[key] for key in essential_keys if key in bounded}
    return json.dumps(essential, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_handler(
    sandbox_handler: Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]],
) -> Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]]:
    def handle(
        payload: dict[str, Any],
        checkpoint: dict[str, Any] | None,
        context: TaskContext,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != _ALLOWED_PAYLOAD_KEYS:
            raise ValueError("Factual verification payload has missing or unsupported fields.")
        knowledge_id = payload.get("knowledge_id")
        if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
            raise ValueError("knowledge_id must be a positive integer.")
        if payload.get("oracle_reviewed") is not True:
            raise ValueError("A human must explicitly attest that this test is checked against an independent oracle.")
        oracle_review_note = payload.get("oracle_review_note")
        if not isinstance(oracle_review_note, str) or len(oracle_review_note.strip()) < 20 or len(oracle_review_note) > 1000:
            raise ValueError("oracle_review_note must explain the independent reference review in 20–1000 characters.")
        oracle_review_id = payload.get("oracle_review_id")
        if isinstance(oracle_review_id, bool) or not isinstance(oracle_review_id, int) or oracle_review_id < 1:
            raise ValueError("oracle_review_id must identify a persisted oracle review.")
        test_name = payload.get("test_name")
        if not isinstance(test_name, str) or not test_name.strip() or len(test_name) > 300:
            raise ValueError("test_name must contain 1–300 characters.")
        source_code = payload.get("source_code")
        candidate = get_verification_candidate(knowledge_id)
        _validate_domain_test_harness(source_code, candidate["runner_language"])
        timeout_seconds = payload.get("timeout_seconds")
        max_output_bytes = payload.get("max_output_bytes")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be an integer from 1 to 120.")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1 <= max_output_bytes <= 65536:
            raise ValueError("max_output_bytes must be an integer from 1 to 65536.")
        if not sandbox_consent.is_enabled():
            raise ValueError("Sandbox execution requires explicit, current user consent.")

        original_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        with learning_connection() as connection:
            oracle_review = factual_knowledge.get_oracle_review(
                connection,
                oracle_review_id,
                knowledge_id=knowledge_id,
                claim_hash=candidate["claim_hash"],
                test_source_hash=original_hash,
            )
        if oracle_review is None:
            raise ValueError("Persisted independent-oracle review does not match this claim revision and exact test source.")
        with learning_connection() as connection:
            evidence_rows = connection.execute(
                """SELECT source_type, source_ref, source_hash, excerpt, applicability_json
                   FROM factual_evidence
                   WHERE knowledge_id=? AND source_type IN ('official_docs', 'source_code')
                   ORDER BY id LIMIT 4""",
                (knowledge_id,),
            ).fetchall()
        if len(evidence_rows) > 3:
            raise ValueError("Independent factual review is limited to three official source excerpts; curate evidence before testing.")
        usable_evidence = []
        for evidence_row in evidence_rows:
            evidence_item = dict(evidence_row)
            parsed_source_url = urlparse(str(evidence_item.get("source_ref", "")))
            excerpt = evidence_item.get("excerpt")
            if (
                evidence_item.get("source_type") in {"official_docs", "source_code"}
                and parsed_source_url.scheme == "https"
                and parsed_source_url.hostname
                and len(str(evidence_item.get("source_ref", ""))) <= 1500
                and isinstance(excerpt, str)
                and excerpt.strip()
            ):
                usable_evidence.append(evidence_item)
        if not usable_evidence:
            raise ValueError("Independent factual review requires a bounded HTTPS source excerpt.")
        source_items = usable_evidence[:3]
        # Bind the human attestation to an exact excerpt the reviewer will see.
        oracle_evidence = next(
            (
                item for item in source_items
                if item["source_ref"] == oracle_review["oracle_source_url"]
                and hashlib.sha256(str(item["excerpt"]).encode("utf-8")).hexdigest()
                    == oracle_review["oracle_source_hash"]
            ),
            None,
        )
        if oracle_evidence is None:
            raise ValueError(
                "Oracle review must match the URL and SHA-256 of an exact stored source excerpt."
            )
        # Re-fetch every evidence page from the official-host allowlist and require
        # each quoted excerpt to remain present in the live page before model review.
        live_source_hashes: dict[str, str] = {}
        for evidence_item in source_items:
            live_source_hashes[evidence_item["source_ref"]] = _verify_live_source_excerpt(
                str(evidence_item["source_ref"]), str(evidence_item["excerpt"])
            )
        source_blocks = []
        source_versions = []
        for index, evidence_item in enumerate(source_items, start=1):
            try:
                applicability = json.loads(evidence_item.get("applicability_json") or "{}")
            except (TypeError, ValueError):
                applicability = {}
            source_version_item = applicability.get("source_version") or applicability.get("version")
            if not isinstance(source_version_item, str) or not source_version_item.strip():
                source_version_item = ""
            url_version = factual_semantic_review.extract_url_version(str(evidence_item["source_ref"]))
            if not url_version:
                # Major-only or unversioned documentation URLs do not establish minor-version compatibility.
                source_version_item = "unspecified"
            elif source_version_item:
                versions_agree = (
                    factual_semantic_review.versions_compatible(url_version, source_version_item)
                    or factual_semantic_review.versions_compatible(source_version_item, url_version)
                )
                if not versions_agree:
                    source_version_item = "conflict_or_unknown"
            else:
                source_version_item = url_version
            source_versions.append(source_version_item)
            source_blocks.append(
                f"[Source {index}] URL: {str(evidence_item['source_ref'])[:250]}\n"
                f"Version: {str(source_version_item)[:120]}\n"
                f"Stored source hash: {str(evidence_item.get('source_hash', ''))[:128]}\n"
                f"Excerpt: {str(evidence_item.get('excerpt', '')).strip()[:700]}"
            )
        excerpt = "\n\n".join(source_blocks)[:4000]
        excerpt_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        compatible_versions = [
            version for version in source_versions
            if factual_semantic_review.versions_compatible(version, candidate["environment_version"])
        ]
        source_version = (
            max(compatible_versions, key=lambda value: len(value.split(".")))
            if len(compatible_versions) == len(source_versions) and compatible_versions
            else "conflict_or_unknown"
        )
        evidence = source_items[0]
        try:
            semantic_decision = factual_semantic_review.review_claim_against_source(
                claim=candidate["claim"],
                claim_hash=candidate["claim_hash"],
                source_excerpt=excerpt,
                source_url=evidence["source_ref"],
                source_hash=excerpt_hash,
                source_version=source_version,
                claim_version=candidate["environment_version"],
                environment_name=candidate["environment_name"],
                environment_version=candidate["environment_version"],
                test_source=source_code,
            )
        except (ValueError, RuntimeError) as exc:
            raise ValueError("Independent factual semantic review failed closed: " + str(exc)[:300]) from exc
        if not semantic_decision.accepted:
            raise ValueError("Independent factual review did not pass the fail-closed rubric.")
        wrapped, completion_marker = _wrapped_harness(source_code, candidate["runner_language"])
        context.checkpoint({
            "phase": "factual_claim_test_started",
            "knowledge_id": knowledge_id,
            "test_name": test_name,
            "test_source_sha256": original_hash,
            "claim_hash": candidate["claim_hash"],
        })
        result = sandbox_handler({
            "source_code": wrapped,
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
            "language": candidate["runner_language"],
        }, checkpoint, context)
        if context.control_status() != "running":
            raise RuntimeError("Factual claim test stopped before evidence could be recorded.")
        if not sandbox_consent.is_enabled():
            raise RuntimeError("Factual claim test stopped because sandbox consent was revoked.")

        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        runtime_version, harness_completed, _bounded_stdout = _runtime_version(stdout, completion_marker, candidate["runner_language"])
        execution_status = str(result.get("execution_status", "unknown"))
        exit_code = result.get("exit_code")
        log_hash = hashlib.sha256((stdout + "\n" + stderr).encode("utf-8")).hexdigest()
        outcome = "inconclusive"
        if runtime_version is None or execution_status == "unavailable":
            outcome = "environment_failure"
        elif runtime_version != candidate["environment_version"]:
            outcome = "environment_failure"
        elif harness_completed and execution_status == "succeeded" and exit_code == 0:
            outcome = "passed"

        summary = {
            "runner": "docker-sandbox",
            "runtime_name": candidate["environment_name"],
            "execution_status": execution_status,
            "exit_code": exit_code,
            "runtime_version": runtime_version,
            "configured_image": str(result.get("image", ""))[:200],
            "duration_ms": result.get("duration_ms"),
            "claim_hash_at_start": candidate["claim_hash"],
            "test_source_sha256": original_hash,
            "oracle_reviewed": True,
            "oracle_review_id": oracle_review_id,
            "semantic_rubric_version": semantic_decision.rubric_version,
            "semantic_review_score": semantic_decision.score,
            "semantic_review_verdict": semantic_decision.verdict,
            "semantic_review_confidence": semantic_decision.confidence,
            "semantic_evaluator_model": semantic_decision.evaluator_model,
            "semantic_evaluator_digest": semantic_decision.evaluator_digest,
            "semantic_review_rationale": (semantic_decision.rationale or "")[:300],
            "semantic_source_url": (semantic_decision.source_url or "")[:300],
            "semantic_source_excerpt_sha256": semantic_decision.source_excerpt_sha256,
            "oracle_review_note": oracle_review_note.strip()[:200],
            "oracle_source_url": oracle_review["oracle_source_url"][:300],
            "oracle_source_hash": oracle_review["oracle_source_hash"],
            "live_source_verified": True,
            "live_source_hashes": {
                hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]: digest
                for url, digest in live_source_hashes.items()
            },
            "output_hash": log_hash,
        }
        summary_json = _bounded_summary_json(summary)
        with learning_connection() as connection:
            current = connection.execute(
                """SELECT claim_hash, evidence_level, lifecycle_status,
                          domain_name, environment_name, environment_version
                   FROM factual_knowledge WHERE id=?""",
                (knowledge_id,),
            ).fetchone()
            if (
                current is None
                or current["claim_hash"] != candidate["claim_hash"]
                or current["evidence_level"] != "L1_sourced"
                or current["lifecycle_status"] != "active"
                or current["domain_name"] != candidate["domain_name"]
                or current["environment_name"] != candidate["environment_name"]
                or current["environment_version"] != candidate["environment_version"]
            ):
                outcome = "inconclusive"
                summary["reason"] = "Claim identity or eligibility changed during the sandbox run."
                summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            factual_knowledge.record_test(
                connection,
                knowledge_id,
                test_kind="unit",
                test_name=test_name.strip(),
                environment_name=candidate["environment_name"],
                environment_version=runtime_version or "unknown",
                outcome=outcome,
                result_summary=summary_json,
                command_hash=original_hash,
                log_hash=log_hash,
            )
            promoted = False
            if outcome == "passed" and current is not None:
                factual_knowledge.transition_evidence_level(connection, knowledge_id, "L2_tested")
                promoted = True

        result_payload = {
            "knowledge_id": knowledge_id,
            "test_name": test_name.strip(),
            "outcome": outcome,
            "evidence_level_promoted": promoted,
            "runtime_version": runtime_version,
            "test_source_sha256": original_hash,
            "oracle_review_id": oracle_review_id,
            "semantic_rubric_version": semantic_decision.rubric_version,
            "semantic_review_score": semantic_decision.score,
            "semantic_review_verdict": semantic_decision.verdict,
            "semantic_evaluator_model": semantic_decision.evaluator_model,
            "semantic_evaluator_digest": semantic_decision.evaluator_digest,
            "semantic_review_rationale": semantic_decision.rationale,
            "semantic_source_url": semantic_decision.source_url,
            "semantic_source_excerpt_sha256": semantic_decision.source_excerpt_sha256,
            "log_sha256": log_hash,
        }
        context.checkpoint({"phase": "factual_claim_test_recorded", **result_payload})
        return result_payload

    return handle


def handler_from_environment(
    sandbox_handler: Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any], dict[str, Any] | None, TaskContext], dict[str, Any]] | None:
    """Expose claim verification only when the administrator-configured sandbox exists."""
    if sandbox_handler is None:
        sandbox_handler = sandbox_tasks.handler_from_environment(allow_language_runners=True)
    if sandbox_handler is None:
        return None
    return _build_handler(sandbox_handler)
