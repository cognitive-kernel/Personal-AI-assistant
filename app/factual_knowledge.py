"""Policy-enforcing API for versioned, domain-scoped factual knowledge.

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
    environment_name: str = "python",
    scope: dict[str, Any] | None = None,
    risk_level: str = "low",
) -> int:
    normalized = normalize_claim(claim)
    if not isinstance(environment_version, str) or not environment_version.strip():
        raise KnowledgePolicyError("environment_version is required")
    if not isinstance(environment_name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", environment_name):
        raise KnowledgePolicyError("environment_name must be a normalized domain identifier")
    scope_value = scope or {}
    scope_json = json.dumps(scope_value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    identity = json.dumps(
        {"claim": normalized.casefold(), "scope": scope_value, "environment_name": environment_name,
         "environment_version": environment_version},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    connection.execute(
        """INSERT INTO factual_knowledge(
               claim, normalized_claim, claim_hash, scope_json,
               environment_name, environment_version, risk_level
           ) VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(claim_hash, environment_name, environment_version) DO NOTHING""",
        (claim.strip(), normalized, digest, scope_json, environment_name, environment_version, risk_level),
    )
    row = connection.execute(
        """SELECT id FROM factual_knowledge
           WHERE claim_hash = ? AND environment_name = ? AND environment_version = ?""",
        (digest, environment_name, environment_version),
    ).fetchone()
    if row is None:
        raise KnowledgePolicyError("claim could not be persisted")
    return int(row[0])


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
    applicability_json = json.dumps(applicability or {}, sort_keys=True, ensure_ascii=False)
    connection.execute(
        """INSERT INTO factual_evidence(
               knowledge_id, source_type, source_ref, source_hash, excerpt, applicability_json
           ) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(knowledge_id, source_ref, source_hash) DO NOTHING""",
        (knowledge_id, source_type, source_ref, source_hash, excerpt, applicability_json),
    )
    row = connection.execute(
        """SELECT id FROM factual_evidence
           WHERE knowledge_id = ? AND source_ref = ? AND source_hash = ?""",
        (knowledge_id, source_ref, source_hash),
    ).fetchone()
    if row is None:
        raise KnowledgePolicyError("evidence could not be persisted")
    return int(row[0])


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


_RETRIEVAL_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "does", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "why", "with", "should",
    "از", "به", "در", "و", "که", "برای", "چگونه", "چیست", "چطور", "آیا",
    "با", "را", "این", "آن", "می", "شود", "است", "هستند",
}


def _retrieval_tokens(value: str) -> list[str]:
    normalized = value.casefold().replace("ي", "ی").replace("ك", "ک")
    return [
        token for token in re.findall(r"[a-z0-9_]+|[\u0600-\u06FF]+", normalized)
        if len(token) >= 2 and token not in _RETRIEVAL_STOPWORDS
    ]


def retrieve_verified_claims(
    connection: sqlite3.Connection,
    query: str,
    *,
    environment_name: str,
    environment_version: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return only source-backed, active L2/L3 claims for one exact environment.

    This is a read-only lexical candidate retriever, not a truth oracle or an LLM
    judgment. Missing scope, weak trust, conflicting active relations, and missing
    evidence all fail closed. Results include source provenance for downstream use.
    """
    if (
        not isinstance(query, str) or not query.strip() or len(query) > 1000
        or not isinstance(environment_name, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", environment_name)
        or not isinstance(environment_version, str) or not environment_version.strip()
    ):
        return []
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise KnowledgePolicyError("limit must be an integer between 1 and 20")

    tokens = list(dict.fromkeys(_retrieval_tokens(query)))[:32]
    if not tokens:
        return []
    token_predicates = " OR ".join("normalized_claim LIKE ?" for _ in tokens)
    token_values = [f"%{token}%" for token in tokens]
    rows = connection.execute(
        f"""SELECT id, claim, normalized_claim, evidence_level, lifecycle_status,
                   environment_name, environment_version, scope_json, risk_level, updated_at
            FROM factual_knowledge AS k
            WHERE k.environment_name = ?
              AND k.environment_version = ?
              AND k.evidence_level IN ('L2_tested', 'L3_human_verified')
              AND k.lifecycle_status = 'active'
              AND (k.risk_level NOT IN ('high', 'critical') OR k.evidence_level = 'L3_human_verified')
              AND EXISTS (
                  SELECT 1 FROM factual_evidence e WHERE e.knowledge_id = k.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM knowledge_relations r
                  JOIN factual_knowledge other
                    ON other.id = CASE
                        WHEN r.knowledge_id = k.id THEN r.related_knowledge_id
                        ELSE r.knowledge_id
                    END
                  WHERE r.relation_type = 'contradicts'
                    AND (r.knowledge_id = k.id OR r.related_knowledge_id = k.id)
                    AND other.lifecycle_status IN ('active', 'conflicting', 'needs_review')
              )
              AND ({token_predicates})
            ORDER BY CASE k.evidence_level
                       WHEN 'L3_human_verified' THEN 0 ELSE 1 END,
                     k.updated_at DESC, k.id DESC
            LIMIT 500""",
        [environment_name, environment_version, *token_values],
    ).fetchall()

    query_tokens = set(tokens)
    ranked = []
    for row in rows:
        claim_tokens = set(_retrieval_tokens(row[2]))
        overlap = len(query_tokens & claim_tokens)
        if overlap == 0:
            continue
        score = round(overlap / len(query_tokens), 4)
        evidence_rows = connection.execute(
            """SELECT source_type, source_ref, source_hash, excerpt, applicability_json
               FROM factual_evidence WHERE knowledge_id = ? ORDER BY id""",
            (row[0],),
        ).fetchall()
        evidence = []
        for item in evidence_rows:
            try:
                applicability = json.loads(item[4]) if item[4] else {}
            except (json.JSONDecodeError, TypeError):
                applicability = {}
            if not isinstance(applicability, dict):
                applicability = {}
            evidence.append({
                "source_type": item[0],
                "source_ref": item[1],
                "source_hash": item[2],
                "excerpt": item[3][:2500] if isinstance(item[3], str) else "",
                "applicability": applicability,
            })
        if not evidence:
            continue
        try:
            scope = json.loads(row[7]) if row[7] else {}
        except (json.JSONDecodeError, TypeError):
            scope = {}
        if not isinstance(scope, dict):
            scope = {}
        ranked.append({
            "id": row[0],
            "claim": row[1],
            "evidence_level": row[3],
            "lifecycle_status": row[4],
            "environment_name": row[5],
            "environment_version": row[6],
            "scope": scope,
            "risk_level": row[8],
            "score": score,
            "evidence": evidence,
        })
    ranked.sort(
        key=lambda item: (
            -item["score"],
            0 if item["evidence_level"] == "L3_human_verified" else 1,
            -item["id"],
        )
    )
    return ranked[:limit]


def list_claim_revisions(
    connection: sqlite3.Connection, knowledge_id: int
) -> list[dict[str, Any]]:
    """Return the immutable content history for one factual claim."""
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
        raise KnowledgePolicyError("knowledge_id must be a positive integer")
    exists = connection.execute(
        "SELECT 1 FROM factual_knowledge WHERE id = ?", (knowledge_id,)
    ).fetchone()
    if exists is None:
        raise KnowledgePolicyError(f"unknown knowledge id: {knowledge_id}")
    rows = connection.execute(
        """SELECT revision_number, claim, normalized_claim, claim_hash, scope_json,
                  environment_name, environment_version, recorded_at
           FROM factual_claim_revisions
           WHERE knowledge_id = ? ORDER BY revision_number""",
        (knowledge_id,),
    ).fetchall()
    keys = (
        "revision_number", "claim", "normalized_claim", "claim_hash", "scope_json",
        "environment_name", "environment_version", "recorded_at",
    )
    return [dict(zip(keys, row)) for row in rows]


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





def list_retrievable_claims(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return only fresh, active, source-backed L2/L3 claims eligible for RAG.

    This is a strict eligibility filter, not a semantic retriever and not proof
    that a claim is true. Unverified, failed, conflicting, superseded, stale,
    source-less, or inapplicably tested claims are excluded.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise KnowledgePolicyError("limit must be an integer between 1 and 500")

    rows = connection.execute(
        """SELECT k.id, k.claim, k.normalized_claim, k.scope_json, k.environment_name,
                  k.environment_version, k.evidence_level, k.lifecycle_status,
                  k.risk_level, k.verified_at, k.reverify_after
           FROM factual_knowledge AS k
           WHERE k.domain = 'python'
             AND k.lifecycle_status = 'active'
             AND k.evidence_level IN ('L2_tested', 'L3_human_verified')
             AND (k.reverify_after IS NULL OR k.reverify_after > CURRENT_TIMESTAMP)
             AND EXISTS (
                 SELECT 1 FROM factual_evidence AS e
                 WHERE e.knowledge_id = k.id
                   AND length(trim(e.source_ref)) > 0
                   AND length(trim(e.source_hash)) > 0
             )
             AND EXISTS (
                 SELECT 1 FROM factual_tests AS t
                 WHERE t.knowledge_id = k.id
                   AND t.outcome = 'passed'
                   AND t.environment_name = k.environment_name
                   AND t.environment_version = k.environment_version
             )
           ORDER BY
             CASE k.evidence_level WHEN 'L3_human_verified' THEN 0 ELSE 1 END,
             k.updated_at DESC, k.id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["scope"] = json.loads(item.pop("scope_json"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise KnowledgePolicyError(
                f"claim {item.get('id')} has invalid scope JSON"
            ) from exc
        evidence_rows = connection.execute(
            """SELECT source_type, source_ref, source_hash, excerpt, captured_at,
                      applicability_json
               FROM factual_evidence
               WHERE knowledge_id = ?
               ORDER BY id""",
            (item["id"],),
        ).fetchall()
        item["evidence"] = []
        for evidence in evidence_rows:
            entry = dict(evidence)
            try:
                entry["applicability"] = json.loads(entry.pop("applicability_json"))
            except (TypeError, json.JSONDecodeError) as exc:
                raise KnowledgePolicyError(
                    f"claim {item['id']} has invalid evidence applicability JSON"
                ) from exc
            item["evidence"].append(entry)
        test_rows = connection.execute(
            """SELECT test_kind, test_name, environment_name, environment_version,
                      outcome, result_summary, executed_at
               FROM factual_tests
               WHERE knowledge_id = ?
                 AND outcome = 'passed'
                 AND environment_name = ?
                 AND environment_version = ?
               ORDER BY executed_at DESC, id DESC""",
            (item["id"], item["environment_name"], item["environment_version"]),
        ).fetchall()
        item["passing_tests"] = [dict(test) for test in test_rows]
        result.append(item)
    return result



_RETRIEVAL_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "by", "can", "do",
    "for", "from", "has", "have", "how", "in", "is", "it", "of", "on", "or",
    "the", "their", "this", "to", "what", "when", "which", "with", "why",
    "پایتون", "چطور", "چگونه", "چیست", "برای", "از", "به", "در", "با", "و",
    "یا", "را", "این", "آن", "چه", "چرا", "است", "هست", "یک",
}


def _retrieval_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+|[\u0600-\u06FF]+", text.casefold())
        if len(token) >= 2 and token not in _RETRIEVAL_STOP_WORDS
    }


def search_retrievable_claims(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return query-relevant eligible claims using conservative lexical matching.

    This is a small lexical retrieval layer, not semantic search. At least two
    distinct query terms must match a claim to avoid injecting broad, irrelevant
    context. All trust, freshness, source and test gates are enforced by
    list_retrievable_claims before relevance ranking.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    if len(query) > 3000:
        raise KnowledgePolicyError("query must be at most 3000 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise KnowledgePolicyError("limit must be an integer between 1 and 20")
    query_tokens = _retrieval_tokens(query)
    if len(query_tokens) < 2:
        return []

    eligible = list_retrievable_claims(connection, limit=500)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for item in eligible:
        claim_tokens = _retrieval_tokens(
            str(item.get("normalized_claim", "")) + " " +
            json.dumps(item.get("scope", {}), ensure_ascii=False, sort_keys=True)
        )
        overlap = len(query_tokens & claim_tokens)
        if overlap < 2:
            continue
        level_rank = 0 if item.get("evidence_level") == "L3_human_verified" else 1
        ranked.append((overlap, level_rank, item))
    ranked.sort(
        key=lambda entry: (
            -entry[0],
            entry[1],
            -int(entry[2]["id"]),
        ),
        reverse=False,
    )
    return [item for _overlap, _level_rank, item in ranked[:limit]]
