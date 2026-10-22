"""Loopback-only, consent-gated API for testing sourced Python factual claims."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from . import factual_knowledge, factual_verification_tasks, sandbox_consent, sandbox_tasks, task_manager
from .db import learning_connection
from .task_routes import require_loopback_client

router = APIRouter(
    prefix="/api/factual-knowledge",
    tags=["factual-knowledge"],
    dependencies=[Depends(require_loopback_client)],
)


class FactualSourceReviewRequest(BaseModel):
    confirm: StrictBool
    source_supports_claim: StrictBool
    review_note: str = Field(min_length=20, max_length=1000)

    model_config = ConfigDict(extra="forbid")


class FactualClaimTestRequest(BaseModel):
    confirm: StrictBool
    oracle_reviewed: StrictBool
    oracle_review_note: str = Field(min_length=20, max_length=1000)
    test_name: str = Field(min_length=1, max_length=300)
    source_code: str = Field(min_length=1, max_length=65536)
    timeout_seconds: StrictInt = Field(default=10, ge=1, le=120)
    max_output_bytes: StrictInt = Field(default=32768, ge=1, le=65536)
    priority: StrictInt = Field(default=0, ge=-100000, le=100000)

    model_config = ConfigDict(extra="forbid")


def _configured() -> bool:
    try:
        return sandbox_tasks.handler_from_environment() is not None
    except ValueError:
        return False


@router.get("/test-history")
def list_factual_test_history(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Return bounded factual-test outcomes with hashes and current claim state."""
    try:
        with learning_connection() as connection:
            rows = connection.execute(
                """SELECT t.id AS test_id, t.knowledge_id, k.claim, k.domain_name,
                          t.test_kind, t.test_name, t.environment_name,
                          t.environment_version AS test_environment_version,
                          t.outcome, t.result_summary, t.command_hash, t.log_hash,
                          t.executed_at AS created_at, k.evidence_level, k.lifecycle_status,
                          k.environment_version AS current_environment_version
                   FROM factual_tests AS t
                   JOIN factual_knowledge AS k ON k.id=t.knowledge_id
                   ORDER BY t.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "count": len(rows), "limit": limit}
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Factual test history is unavailable.") from exc


@router.post("/{knowledge_id}/run-test", status_code=status.HTTP_202_ACCEPTED)
def queue_factual_claim_test(
    knowledge_id: int,
    request: FactualClaimTestRequest,
) -> dict[str, Any]:
    if request.confirm is not True:
        raise HTTPException(status_code=422, detail="Explicit confirmation to execute this test harness is required.")
    if request.oracle_reviewed is not True:
        raise HTTPException(status_code=422, detail="Explicit attestation that the test matches an independent oracle is required.")
    try:
        factual_verification_tasks.get_verification_candidate(knowledge_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Factual claim not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        factual_verification_tasks.validate_test_harness(request.source_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        consent = sandbox_consent.get_consent()
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Sandbox consent storage is unavailable.") from exc
    if not consent["enabled"]:
        raise HTTPException(status_code=409, detail="Factual claim testing requires explicit current sandbox consent.")
    if not _configured():
        raise HTTPException(status_code=409, detail="Factual claim testing requires a configured digest-pinned Docker sandbox.")

    source_hash = hashlib.sha256(request.source_code.encode("utf-8")).hexdigest()
    payload = {
        "knowledge_id": knowledge_id,
        "oracle_reviewed": request.oracle_reviewed,
        "oracle_review_note": request.oracle_review_note.strip(),
        "test_name": request.test_name.strip(),
        "source_code": request.source_code,
        "timeout_seconds": request.timeout_seconds,
        "max_output_bytes": request.max_output_bytes,
    }
    try:
        task = task_manager.create_task(
            factual_verification_tasks.FACTUAL_VERIFY_TASK_TYPE,
            payload,
            priority=request.priority,
            max_attempts=1,
            dedupe_key=f"factual-verify:{knowledge_id}:{source_hash}:{request.test_name.strip()}",
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Factual claim test could not be queued.") from exc
    return {
        "task": task,
        "message": "Factual claim test queued in the isolated sandbox; any L2 promotion requires the reviewed independent oracle and an exact Python-version match.",
    }


@router.get("/review-queue")
def list_factual_source_review_queue(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List bounded L0 claims with traceable sources awaiting explicit human review."""
    try:
        with learning_connection() as connection:
            rows = connection.execute(
                """SELECT k.id, k.claim, k.domain_name, k.environment_name,
                          k.environment_version, k.evidence_level, k.lifecycle_status,
                          k.risk_level, k.created_at
                   FROM factual_knowledge AS k
                   WHERE k.evidence_level='L0_raw'
                     AND k.lifecycle_status='unverified'
                     AND EXISTS (
                         SELECT 1 FROM factual_evidence AS e
                         WHERE e.knowledge_id=k.id
                     )
                   ORDER BY k.created_at, k.id
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                evidence = connection.execute(
                    """SELECT source_type, source_ref, source_hash, excerpt, applicability_json
                       FROM factual_evidence WHERE knowledge_id=? ORDER BY id""",
                    (item["id"],),
                ).fetchall()
                item["evidence"] = [dict(source) for source in evidence]
                items.append(item)
        return {"items": items, "count": len(items), "limit": limit}
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Factual source review queue is unavailable.") from exc


@router.get("/test-queue")
def list_factual_test_queue(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List sourced, active Python L1 claims that may be manually submitted for testing."""
    try:
        with learning_connection() as connection:
            rows = connection.execute(
                """SELECT k.id, k.claim, k.domain_name, k.environment_name,
                          k.environment_version, k.evidence_level, k.lifecycle_status,
                          k.risk_level, k.updated_at
                   FROM factual_knowledge AS k
                   WHERE k.domain_name='python'
                     AND k.environment_name='python'
                     AND k.evidence_level='L1_sourced'
                     AND k.lifecycle_status='active'
                     AND k.risk_level NOT IN ('high','critical')
                     AND EXISTS (
                         SELECT 1 FROM factual_evidence AS e
                         WHERE e.knowledge_id=k.id
                           AND e.source_type IN ('official_docs','source_code')
                           AND e.source_ref LIKE 'https://%'
                           AND length(trim(e.source_hash)) > 0
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM factual_tests AS t
                         WHERE t.knowledge_id=k.id
                           AND t.outcome='passed'
                           AND t.environment_name=k.environment_name
                           AND t.environment_version=k.environment_version
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM task_queue AS q
                         WHERE q.task_type='factual.verify_claim'
                           AND json_extract(q.payload_json, '$.knowledge_id')=k.id
                           AND q.status IN ('queued','running','pause_requested','cancel_requested')
                     )
                   ORDER BY k.updated_at DESC, k.id DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                evidence = connection.execute(
                    """SELECT source_type, source_ref, source_hash, excerpt
                       FROM factual_evidence
                       WHERE knowledge_id=?
                         AND source_type IN ('official_docs','source_code')
                         AND source_ref LIKE 'https://%'
                         AND length(trim(source_hash)) > 0
                       ORDER BY id""",
                    (item["id"],),
                ).fetchall()
                item["evidence"] = [dict(source) for source in evidence]
                items.append(item)
        return {"items": items, "count": len(items), "limit": limit}
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Factual test queue is unavailable.") from exc


@router.post("/{knowledge_id}/approve-source")
def approve_factual_source(
    knowledge_id: int,
    request: FactualSourceReviewRequest,
) -> dict[str, Any]:
    """Record explicit local-human review before allowing L0 -> L1 source promotion."""
    if request.confirm is not True or request.source_supports_claim is not True:
        raise HTTPException(
            status_code=422,
            detail="Explicit confirmation that the listed source supports this exact claim is required.",
        )
    note = request.review_note.strip()
    if len(note) < 20:
        raise HTTPException(status_code=422, detail="A meaningful human review note is required.")

    try:
        with learning_connection() as connection:
            row = connection.execute(
                """SELECT id, claim, evidence_level, lifecycle_status, risk_level,
                          environment_name, environment_version
                   FROM factual_knowledge WHERE id=?""",
                (knowledge_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Factual claim not found.")
            if row["evidence_level"] != "L0_raw" or row["lifecycle_status"] != "unverified":
                raise HTTPException(status_code=409, detail="Only unreviewed L0 claims can be source-approved.")
            evidence = connection.execute(
                """SELECT source_type, source_ref, source_hash
                   FROM factual_evidence WHERE knowledge_id=? ORDER BY id""",
                (knowledge_id,),
            ).fetchall()
            eligible = []
            for source in evidence:
                parsed = urlparse(source["source_ref"])
                if (
                    source["source_type"] in {"official_docs", "source_code"}
                    and parsed.scheme == "https"
                    and bool(parsed.hostname)
                    and not parsed.username
                    and not parsed.password
                    and isinstance(source["source_hash"], str)
                    and source["source_hash"].strip()
                ):
                    eligible.append(source)
            if not eligible:
                raise HTTPException(
                    status_code=409,
                    detail="At least one traceable HTTPS official-documentation or source-code reference is required.",
                )

            before_level = row["evidence_level"]
            before_status = row["lifecycle_status"]
            factual_knowledge.transition_evidence_level(connection, knowledge_id, "L1_sourced")
            after_status = "needs_review" if row["risk_level"] in {"high", "critical"} else "active"
            factual_knowledge.transition_lifecycle(connection, knowledge_id, after_status)
            source_hashes = sorted({source["source_hash"] for source in eligible})
            connection.execute(
                """INSERT INTO factual_knowledge_manual_reviews(
                       knowledge_id, reviewer, action, evidence_level_before,
                       evidence_level_after, lifecycle_before, lifecycle_after,
                       source_hashes_json, review_note
                   ) VALUES (?, 'local_user', 'source_approved', ?, 'L1_sourced', ?, ?, ?, ?)""",
                (
                    knowledge_id, before_level, before_status, after_status,
                    json.dumps(source_hashes, ensure_ascii=False, separators=(",", ":")), note,
                ),
            )
        return {
            "knowledge_id": knowledge_id,
            "evidence_level": "L1_sourced",
            "lifecycle_status": after_status,
            "message": (
                "Source review recorded. This is not proof from an independent test; "
                "L2 promotion still requires a passing applicable test."
            ),
        }
    except HTTPException:
        raise
    except factual_knowledge.KnowledgePolicyError as exc:
        raise HTTPException(status_code=409, detail="Source review could not be applied under current knowledge policy.") from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Factual source review could not be saved.") from exc


@router.post("/{knowledge_id}/reject-source")
def reject_factual_source(
    knowledge_id: int,
    request: FactualSourceReviewRequest,
) -> dict[str, Any]:
    """Quarantine a reviewed source/claim without asserting that the claim is false."""
    if request.confirm is not True or request.source_supports_claim is not False:
        raise HTTPException(
            status_code=422,
            detail="Explicit confirmation that the current evidence does not adequately support this claim is required.",
        )
    note = request.review_note.strip()
    if len(note) < 20:
        raise HTTPException(status_code=422, detail="A meaningful human review note is required.")

    try:
        with learning_connection() as connection:
            row = connection.execute(
                """SELECT id, evidence_level, lifecycle_status
                   FROM factual_knowledge WHERE id=?""",
                (knowledge_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Factual claim not found.")
            if row["evidence_level"] != "L0_raw" or row["lifecycle_status"] != "unverified":
                raise HTTPException(status_code=409, detail="Only unreviewed L0 claims can be source-rejected.")
            evidence = connection.execute(
                """SELECT source_hash FROM factual_evidence
                   WHERE knowledge_id=? ORDER BY id""",
                (knowledge_id,),
            ).fetchall()
            if not evidence:
                raise HTTPException(status_code=409, detail="The claim has no evidence to review.")
            source_hashes = sorted({
                str(item["source_hash"]).strip()
                for item in evidence
                if isinstance(item["source_hash"], str) and item["source_hash"].strip()
            })
            factual_knowledge.transition_lifecycle(connection, knowledge_id, "needs_review")
            connection.execute(
                """INSERT INTO factual_knowledge_manual_reviews(
                       knowledge_id, reviewer, action, evidence_level_before,
                       evidence_level_after, lifecycle_before, lifecycle_after,
                       source_hashes_json, review_note
                   ) VALUES (?, 'local_user', 'source_rejected', ?, ?, ?, 'needs_review', ?, ?)""",
                (
                    knowledge_id,
                    row["evidence_level"],
                    row["evidence_level"],
                    row["lifecycle_status"],
                    json.dumps(source_hashes, ensure_ascii=False, separators=(",", ":")),
                    note,
                ),
            )
        return {
            "knowledge_id": knowledge_id,
            "evidence_level": "L0_raw",
            "lifecycle_status": "needs_review",
            "message": (
                "Source review recorded. The claim remains unverified and is quarantined "
                "from normal retrieval; this does not establish that the claim is false."
            ),
        }
    except HTTPException:
        raise
    except factual_knowledge.KnowledgePolicyError as exc:
        raise HTTPException(status_code=409, detail="Source rejection could not be applied under current knowledge policy.") from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Source rejection could not be saved.") from exc
