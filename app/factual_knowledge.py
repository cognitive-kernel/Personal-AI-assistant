"""Small, policy-enforcing API for the Python factual-knowledge store.

LLM review output is data only; this module and SQLite constraints own transitions.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

EVIDENCE_LEVELS = ("L0_raw", "L1_sourced", "L2_tested", "L3_human_verified")
LIFECYCLE_STATUSES = ("unverified", "active", "conflicting", "failed", "superseded", "needs_review")
RELATION_TYPES = ("supersedes", "contradicts", "depends_on")
_ALLOWED_LEVEL_TRANSITIONS = {
    "L0_raw": {"L1_sourced"},
    "L1_sourced": {"L2_tested"},
    "L2_tested": {"L3_human_verified"},
    "L3_human_verified": set(),
}
_ALLOWED_STATUS_TRANSITIONS = {
    "unverified": {"active", "needs_review", "failed"},
    "active": {"conflicting", "failed", "superseded", "needs_review"},
    "needs_review": {"active", "conflicting", "failed", "superseded"},
    "conflicting": {"active", "failed", "superseded", "needs_review"},
    "failed": {"needs_review", "superseded"},
    "superseded": {"needs_review"},
}


class KnowledgePolicyError(ValueError):
    """A requested factual-knowledge operation violates the lifecycle policy."""


@dataclass(frozen=True)
class Claim:
    id: int
    claim: str
    evidence_level: str
    lifecycle_status: str
    environment_name: str
    environment_version: str


def normalize_claim(claim: str) -> str:
    """Stable conservative normalization; do not erase punctuation or semantic qualifiers."""
    if not isinstance(claim, str) or not claim.strip():
        raise KnowledgePolicyError("claim must be a non-empty string")
    return re.sub(r"\s+", " ", claim).strip()


def create_claim(
    connection: sqlite3.Connection,
    claim: str,
    *,
    environment_version: str,
    scope: dict[str, Any] | None = None,
    risk_level: str = "low",
) -> int:
    normalized = normalize_claim(claim)
    if not environment_version.strip():
        raise KnowledgePolicyError("environment_version is required")
    digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()
    cursor = connection.execute(
        """INSERT INTO factual_knowledge(
               claim, normalized_claim, claim_hash, scope_json,
               environment_version, risk_level
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (claim.strip(), normalized, digest, json.dumps(scope or {}, sort_keys=True), environment_version, risk_level),
    )
    return int(cursor.lastrowid)


def add_evidence(
    connection: sqlite3.Connection,
    knowledge_id: int,
    *,
    source_type: str,
    source_ref: str,
    source_hash: str,
    excerpt: str = "",
    applicability: dict[str, Any] | None = None,
) -> int:
    if not source_hash.strip():
        raise KnowledgePolicyError("source_hash is required")
    cursor = connection.execute(
        """INSERT INTO factual_evidence(
               knowledge_id, source_type, source_ref, source_hash, excerpt, applicability_json
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (knowledge_id, source_type, source_ref, source_hash, excerpt,
         json.dumps(applicability or {}, sort_keys=True)),
    )
    return int(cursor.lastrowid)


def record_test(
    connection: sqlite3.Connection,
    knowledge_id: int,
    *,
    test_kind: str,
    test_name: str,
    environment_version: str,
    outcome: str,
    result_summary: str = "",
    command_hash: str | None = None,
    log_hash: str | None = None,
    environment_name: str = "python",
) -> int:
    cursor = connection.execute(
        """INSERT INTO factual_tests(
               knowledge_id, test_kind, test_name, environment_name, environment_version,
               outcome, result_summary, command_hash, log_hash
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (knowledge_id, test_kind, test_name, environment_name, environment_version,
         outcome, result_summary[:2000], command_hash, log_hash),
    )
    return int(cursor.lastrowid)


def transition_evidence_level(
    connection: sqlite3.Connection,
    knowledge_id: int,
    target: str,
    *,
    human_verified_by: str | None = None,
) -> None:
    if target not in EVIDENCE_LEVELS:
        raise KnowledgePolicyError(f"unknown evidence level: {target}")
    row = connection.execute(
        "SELECT evidence_level FROM factual_knowledge WHERE id = ?", (knowledge_id,)
    ).fetchone()
    if row is None:
        raise KnowledgePolicyError(f"unknown knowledge id: {knowledge_id}")
    current = row[0]
    if target not in _ALLOWED_LEVEL_TRANSITIONS[current]:
        raise KnowledgePolicyError(f"invalid evidence-level transition: {current} -> {target}")
    fields = ["evidence_level = ?", "updated_at = CURRENT_TIMESTAMP"]
    values: list[Any] = [target]
    if target == "L3_human_verified":
        if not human_verified_by or not human_verified_by.strip():
            raise KnowledgePolicyError("human_verified_by is required for L3")
        fields.extend(["human_verified_by = ?", "human_verified_at = CURRENT_TIMESTAMP", "verified_at = CURRENT_TIMESTAMP"])
        values.append(human_verified_by.strip())
    values.append(knowledge_id)
    connection.execute(f"UPDATE factual_knowledge SET {', '.join(fields)} WHERE id = ?", values)


def transition_lifecycle(connection: sqlite3.Connection, knowledge_id: int, target: str) -> None:
    if target not in LIFECYCLE_STATUSES:
        raise KnowledgePolicyError(f"unknown lifecycle status: {target}")
    row = connection.execute(
        "SELECT lifecycle_status, evidence_level, risk_level FROM factual_knowledge WHERE id = ?",
        (knowledge_id,),
    ).fetchone()
    if row is None:
        raise KnowledgePolicyError(f"unknown knowledge id: {knowledge_id}")
    current, level, risk = row
    if target not in _ALLOWED_STATUS_TRANSITIONS[current]:
        raise KnowledgePolicyError(f"invalid lifecycle transition: {current} -> {target}")
    if target == "active" and level == "L0_raw":
        raise KnowledgePolicyError("raw knowledge cannot become active")
    if target == "active" and risk in {"high", "critical"} and level != "L3_human_verified":
        raise KnowledgePolicyError("high-risk knowledge requires human verification before activation")
    connection.execute(
        "UPDATE factual_knowledge SET lifecycle_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (target, knowledge_id),
    )


def add_relation(
    connection: sqlite3.Connection, knowledge_id: int, related_knowledge_id: int, relation_type: str
) -> int:
    if relation_type not in RELATION_TYPES:
        raise KnowledgePolicyError(f"unknown relation type: {relation_type}")
    cursor = connection.execute(
        """INSERT INTO knowledge_relations(knowledge_id, related_knowledge_id, relation_type)
           VALUES (?, ?, ?)""",
        (knowledge_id, related_knowledge_id, relation_type),
    )
    return int(cursor.lastrowid)


def get_claim(connection: sqlite3.Connection, knowledge_id: int) -> Claim:
    row = connection.execute(
        """SELECT id, claim, evidence_level, lifecycle_status, environment_name, environment_version
           FROM factual_knowledge WHERE id = ?""",
        (knowledge_id,),
    ).fetchone()
    if row is None:
        raise KnowledgePolicyError(f"unknown knowledge id: {knowledge_id}")
    return Claim(*row)


def review_cache_key(
    *,
    normalized_claim: str,
    source_hash: str,
    model_version: str,
    prompt_version: str,
    scope: dict[str, Any] | None = None,
) -> str:
    """Cache only exact review contexts; changing source/model/prompt/scope invalidates it."""
    normalized = normalize_claim(normalized_claim).casefold()
    payload = {
        "claim": normalized,
        "source_hash": source_hash,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "scope": scope or {},
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_cached_review(connection: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT result_json FROM factual_review_cache WHERE cache_key = ? AND expires_at > CURRENT_TIMESTAMP",
        (cache_key,),
    ).fetchone()
    if row is not None:
        try:
            result = json.loads(row[0])
        except (json.JSONDecodeError, TypeError) as exc:
            raise KnowledgePolicyError("cached review is not valid JSON") from exc
        if not isinstance(result, dict):
            raise KnowledgePolicyError("cached review must be a JSON object")
        return result
    connection.execute("DELETE FROM factual_review_cache WHERE cache_key = ?", (cache_key,))
    return None


def put_cached_review(
    connection: sqlite3.Connection,
    *,
    cache_key: str,
    claim_hash: str,
    source_hash: str,
    model_version: str,
    prompt_version: str,
    scope_hash: str,
    result: dict[str, Any],
    ttl_seconds: int = 86400,
) -> None:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 604800:
        raise KnowledgePolicyError("ttl_seconds must be between 1 and 604800")
    if not isinstance(result, dict):
        raise KnowledgePolicyError("cached review result must be a JSON object")
    encoded = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
    connection.execute(
        """INSERT INTO factual_review_cache(
               cache_key, claim_hash, source_hash, model_version, prompt_version,
               scope_hash, result_json, expires_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?))
           ON CONFLICT(cache_key) DO UPDATE SET
             claim_hash=excluded.claim_hash,
             source_hash=excluded.source_hash,
             model_version=excluded.model_version,
             prompt_version=excluded.prompt_version,
             scope_hash=excluded.scope_hash,
             result_json=excluded.result_json,
             created_at=CURRENT_TIMESTAMP,
             expires_at=excluded.expires_at""",
        (cache_key, claim_hash, source_hash, model_version, prompt_version, scope_hash,
         encoded, f"+{ttl_seconds} seconds"),
    )


