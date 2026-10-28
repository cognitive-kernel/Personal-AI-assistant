"""Policy-enforcing API for versioned, domain-scoped factual knowledge.

LLM review output is data only; this module and SQLite constraints own transitions.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

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
    domain_name: str
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
               claim, normalized_claim, claim_hash, scope_json, domain_name,
               environment_name, environment_version, risk_level
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(claim_hash, environment_name, environment_version) DO NOTHING""",
        (claim.strip(), normalized, digest, scope_json, environment_name, environment_name, environment_version, risk_level),
    )
    row = connection.execute(
        """SELECT id FROM factual_knowledge
           WHERE claim_hash = ? AND domain_name = ? AND environment_name = ? AND environment_version = ?""",
        (digest, environment_name, environment_name, environment_version),
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


def record_oracle_review(
    connection: sqlite3.Connection,
    knowledge_id: int,
    *,
    claim_hash: str,
    test_source_hash: str,
    oracle_source_url: str,
    oracle_source_hash: str,
    review_note: str,
) -> int:
    """Persist a traceable local attestation binding a test to an independent reference."""
    for field_name, value in (
        ("claim_hash", claim_hash),
        ("test_source_hash", test_source_hash),
        ("oracle_source_hash", oracle_source_hash),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise KnowledgePolicyError(f"{field_name} must be a lowercase SHA-256 digest")
    if not isinstance(oracle_source_url, str) or not oracle_source_url.startswith("https://"):
        raise KnowledgePolicyError("oracle_source_url must use HTTPS")
    if not isinstance(review_note, str) or len(review_note.strip()) < 20 or len(review_note) > 1000:
        raise KnowledgePolicyError("review_note must contain 20–1000 characters")
    current = connection.execute(
        "SELECT claim_hash FROM factual_knowledge WHERE id = ?", (knowledge_id,)
    ).fetchone()
    if current is None or current["claim_hash"] != claim_hash:
        raise KnowledgePolicyError("oracle review does not match the current claim revision")
    cursor = connection.execute(
        """INSERT INTO factual_oracle_reviews(
               knowledge_id, claim_hash, test_source_hash, oracle_source_url,
               oracle_source_hash, reviewer, review_note
           ) VALUES (?, ?, ?, ?, ?, 'local_user', ?)""",
        (knowledge_id, claim_hash, test_source_hash, oracle_source_url,
         oracle_source_hash, review_note.strip()),
    )
    return int(cursor.lastrowid)


def get_oracle_review(
    connection: sqlite3.Connection,
    review_id: int,
    *,
    knowledge_id: int,
    claim_hash: str,
    test_source_hash: str,
) -> dict[str, Any] | None:
    """Return an audit record only when it binds the exact claim revision and test."""
    row = connection.execute(
        """SELECT id, knowledge_id, claim_hash, test_source_hash, oracle_source_url,
                  oracle_source_hash, reviewer, review_note, created_at
           FROM factual_oracle_reviews
           WHERE id = ? AND knowledge_id = ? AND claim_hash = ? AND test_source_hash = ?""",
        (review_id, knowledge_id, claim_hash, test_source_hash),
    ).fetchone()
    return dict(row) if row is not None else None


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
        """SELECT id, claim, evidence_level, lifecycle_status, domain_name, environment_name, environment_version
           FROM factual_knowledge WHERE id = ?""",
        (knowledge_id,),
    ).fetchone()
    if row is None:
        raise KnowledgePolicyError(f"unknown knowledge id: {knowledge_id}")
    return Claim(*row)




def mark_due_claims_for_review(
    connection: sqlite3.Connection, *, limit: int = 100
) -> int:
    """Move active claims past their explicit re-verification deadline to review.

    This is a conservative freshness gate, not an automatic truth judgment. It
    records an audit event and never deletes claims or downgrades evidence level.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise KnowledgePolicyError("limit must be an integer between 1 and 500")
    rows = connection.execute(
        """SELECT id, reverify_after
           FROM factual_knowledge
           WHERE lifecycle_status = 'active'
             AND reverify_after IS NOT NULL
             AND datetime(reverify_after) <= CURRENT_TIMESTAMP
           ORDER BY datetime(reverify_after), id
           LIMIT ?""",
        (limit,),
    ).fetchall()
    reviewed = 0
    for row in rows:
        knowledge_id, due_at = int(row[0]), str(row[1])
        source_hashes = [
            str(item[0])
            for item in connection.execute(
                """SELECT DISTINCT source_hash FROM factual_evidence
                   WHERE knowledge_id = ? ORDER BY source_hash""",
                (knowledge_id,),
            ).fetchall()
        ]
        cursor = connection.execute(
            """UPDATE factual_knowledge
               SET lifecycle_status = 'needs_review', updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND lifecycle_status = 'active'
                 AND reverify_after IS NOT NULL
                 AND datetime(reverify_after) <= CURRENT_TIMESTAMP""",
            (knowledge_id,),
        )
        if cursor.rowcount != 1:
            continue
        connection.execute(
            """INSERT INTO factual_knowledge_review_events(
                   knowledge_id, event_type, status_before, status_after,
                   due_at, source_hashes_json, reason
               ) VALUES (?, 'reverification_due', 'active', 'needs_review', ?, ?, ?)""",
            (
                knowledge_id,
                due_at,
                json.dumps(source_hashes, ensure_ascii=False, separators=(",", ":")),
                "Re-verification deadline elapsed; claim excluded from retrieval pending review.",
            ),
        )
        reviewed += 1
    return reviewed


def reverify_due_sources(
    connection: sqlite3.Connection,
    *,
    limit: int = 2,
    extension_days: int = 30,
    fetch_document: Callable[..., Any] | None = None,
) -> int:
    """Re-fetch due public sources and quarantine claims when their supporting excerpt changes.

    This checks source continuity, not claim truth. Network failures and changed
    excerpts move an active claim to needs_review; they never mark it false.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise KnowledgePolicyError("limit must be an integer between 1 and 10")
    if (
        isinstance(extension_days, bool)
        or not isinstance(extension_days, int)
        or not 1 <= extension_days <= 365
    ):
        raise KnowledgePolicyError("extension_days must be an integer between 1 and 365")
    if fetch_document is None:
        from .web_sources import fetch_public_document

        fetch_document = fetch_public_document

    claims = connection.execute(
        """SELECT id, claim_hash
           FROM factual_knowledge
           WHERE lifecycle_status='active'
             AND reverify_after IS NOT NULL
             AND datetime(reverify_after) <= CURRENT_TIMESTAMP
           ORDER BY datetime(reverify_after), id
           LIMIT ?""",
        (limit,),
    ).fetchall()
    checked_claims = 0
    for claim_row in claims:
        knowledge_id, claim_hash = int(claim_row[0]), str(claim_row[1])
        evidence_rows = connection.execute(
            """SELECT id, source_ref, source_hash, excerpt
               FROM factual_evidence
               WHERE knowledge_id=?
                 AND source_type IN ('official_docs','source_code','other')
                 AND source_ref LIKE 'https://%'
               ORDER BY id
               LIMIT 2""",
            (knowledge_id,),
        ).fetchall()
        # Claims with no re-fetchable evidence are handled by mark_due_claims_for_review.
        if not evidence_rows:
            continue

        outcomes: list[str] = []
        for evidence in evidence_rows:
            evidence_id = int(evidence[0])
            source_ref = str(evidence[1])
            expected_hash = str(evidence[2])
            excerpt = str(evidence[3] or "")
            observed_hash: str | None = None
            if len(excerpt.strip()) < 40:
                outcome = "inconclusive"
                note = "Stored source excerpt is too short for a safe continuity comparison."
            else:
                try:
                    host = urlparse(source_ref).hostname or "public source"
                    document = fetch_document(source_ref, host, max_chars=6000)
                    current_text = getattr(document, "text", None)
                    if not isinstance(current_text, str) or not current_text.strip():
                        raise ValueError("Fetched source has no readable text.")
                    observed_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
                    expected_text = " ".join(excerpt.split())
                    normalized_current = " ".join(current_text.split())
                    if expected_text in normalized_current:
                        outcome = "unchanged"
                        note = "Stored supporting excerpt is still present in the fetched public source."
                    else:
                        outcome = "changed"
                        note = "Stored supporting excerpt was not found in the current source snapshot."
                except Exception as exc:
                    outcome = "unavailable"
                    note = f"Source re-fetch failed ({type(exc).__name__}); truth was not inferred."

            connection.execute(
                """INSERT INTO factual_source_checks(
                       knowledge_id, evidence_id, claim_hash, source_ref,
                       expected_source_hash, observed_content_hash, outcome, note
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    knowledge_id, evidence_id, claim_hash, source_ref,
                    expected_hash, observed_hash, outcome, note[:1000],
                ),
            )
            outcomes.append(outcome)

        current = connection.execute(
            "SELECT claim_hash, lifecycle_status FROM factual_knowledge WHERE id=?",
            (knowledge_id,),
        ).fetchone()
        if current is None:
            continue
        if outcomes and all(item == "unchanged" for item in outcomes) and current[0] == claim_hash and current[1] == "active":
            connection.execute(
                """UPDATE factual_knowledge
                   SET reverify_after=datetime('now', ?), updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND claim_hash=? AND lifecycle_status='active'
                     AND reverify_after IS NOT NULL
                     AND datetime(reverify_after) <= CURRENT_TIMESTAMP""",
                (f"+{extension_days} days", knowledge_id, claim_hash),
            )
        elif current[1] == "active":
            transition_lifecycle(connection, knowledge_id, "needs_review")
        checked_claims += 1
    return checked_claims



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
                  environment_name, environment_version, recorded_at, domain_name
           FROM factual_claim_revisions
           WHERE knowledge_id = ? ORDER BY revision_number""",
        (knowledge_id,),
    ).fetchall()
    keys = (
        "revision_number", "claim", "normalized_claim", "claim_hash", "scope_json",
        "environment_name", "environment_version", "recorded_at", "domain_name",
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
    domain: str = "python",
    environment_name: str | None = None,
    environment_version: str | None = None,
) -> list[dict[str, Any]]:
    """Return fresh, active, source-backed L2/L3 claims eligible for RAG.

    Domain is exact. If an environment scope is supplied, both its name and
    version are required and matched exactly. Unverified, inactive, stale,
    source-less, untested, or actively contradicted claims fail closed.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise KnowledgePolicyError("limit must be an integer between 1 and 500")
    if not isinstance(domain, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", domain):
        return []
    if (environment_name is None) != (environment_version is None):
        return []
    if environment_name is not None and (
        not isinstance(environment_name, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", environment_name)
        or not isinstance(environment_version, str)
        or not environment_version.strip()
    ):
        return []

    conditions = [
        "k.domain_name = ?",
        "k.lifecycle_status = 'active'",
        "k.evidence_level IN ('L2_tested', 'L3_human_verified')",
        "(k.reverify_after IS NULL OR k.reverify_after > CURRENT_TIMESTAMP)",
        "(k.risk_level NOT IN ('high', 'critical') OR k.evidence_level = 'L3_human_verified')",
        """EXISTS (
            SELECT 1 FROM factual_evidence AS e
            WHERE e.knowledge_id = k.id
              AND e.source_type IN ('official_docs', 'source_code', 'human_review')
              AND length(trim(e.source_ref)) > 0
              AND length(trim(e.source_hash)) > 0
        )""",
        """EXISTS (
            SELECT 1 FROM factual_tests AS t
            WHERE t.knowledge_id = k.id
              AND t.outcome = 'passed'
              AND t.environment_name = k.environment_name
              AND t.environment_version = k.environment_version
        )""",
        """NOT EXISTS (
            SELECT 1
            FROM knowledge_relations AS r
            JOIN factual_knowledge AS other
              ON other.id = CASE
                  WHEN r.knowledge_id = k.id THEN r.related_knowledge_id
                  ELSE r.knowledge_id
              END
            WHERE r.relation_type = 'contradicts'
              AND (r.knowledge_id = k.id OR r.related_knowledge_id = k.id)
              AND other.lifecycle_status IN ('active', 'conflicting', 'needs_review')
        )""",
    ]
    params: list[Any] = [domain]
    if environment_name is not None and environment_version is not None:
        conditions.extend(["k.environment_name = ?", "k.environment_version = ?"])
        params.extend([environment_name, environment_version])
    params.append(limit)

    rows = connection.execute(
        f"""SELECT k.id, k.claim, k.normalized_claim, k.scope_json, k.domain_name,
                  k.environment_name, k.environment_version, k.evidence_level,
                  k.lifecycle_status, k.risk_level, k.verified_at, k.reverify_after
           FROM factual_knowledge AS k
           WHERE {' AND '.join(conditions)}
           ORDER BY
             CASE k.evidence_level WHEN 'L3_human_verified' THEN 0 ELSE 1 END,
             k.updated_at DESC, k.id DESC
           LIMIT ?""",
        params,
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
    domain: str = "python",
    environment_name: str | None = None,
    environment_version: str | None = None,
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

    if environment_name is None or environment_version is None:
        return []
    eligible = list_retrievable_claims(
        connection,
        limit=500,
        domain=domain,
        environment_name=environment_name,
        environment_version=environment_version,
    )
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

def traverse_knowledge_relations(
    connection: sqlite3.Connection,
    knowledge_id: int,
    *,
    max_depth: int = 2,
    relation_types: tuple[str, ...] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Traverse the persisted claim graph deterministically with cycle and scope guards.

    Traversal follows directed knowledge_relations edges. It returns metadata and
    claim text for explanation/planning; callers must still apply normal retrieval
    eligibility and evidence gates before using a related claim as factual context.
    """
    if isinstance(knowledge_id, bool) or not isinstance(knowledge_id, int) or knowledge_id < 1:
        raise KnowledgePolicyError("knowledge_id must be a positive integer")
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 3:
        raise KnowledgePolicyError("max_depth must be an integer between 1 and 3")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise KnowledgePolicyError("limit must be an integer between 1 and 200")
    if relation_types is not None:
        if not isinstance(relation_types, tuple) or not relation_types or any(
            not isinstance(item, str) or item not in RELATION_TYPES for item in relation_types
        ):
            raise KnowledgePolicyError("relation_types must be a non-empty tuple of supported relation types")
        selected_types = tuple(sorted(set(relation_types)))
    else:
        selected_types = tuple(sorted(RELATION_TYPES))

    if connection.execute("SELECT 1 FROM factual_knowledge WHERE id=?", (knowledge_id,)).fetchone() is None:
        raise KnowledgePolicyError(f"unknown knowledge id: {knowledge_id}")

    frontier: list[tuple[int, int, tuple[str, ...]]] = [(knowledge_id, 0, ())]
    visited = {knowledge_id}
    results: list[dict[str, Any]] = []
    placeholders = ",".join("?" for _ in selected_types)
    while frontier and len(results) < limit:
        current_id, depth, path = frontier.pop(0)
        if depth >= max_depth:
            continue
        cursor = connection.execute(
            f"""SELECT r.related_knowledge_id, r.relation_type, k.claim,
                       k.domain_name, k.environment_name, k.environment_version,
                       k.evidence_level, k.lifecycle_status
                FROM knowledge_relations AS r
                JOIN factual_knowledge AS k ON k.id=r.related_knowledge_id
                WHERE r.knowledge_id=? AND r.relation_type IN ({placeholders})
                ORDER BY r.relation_type, r.related_knowledge_id
                LIMIT ?""",
            (current_id, *selected_types, limit),
        )
        columns = [column[0] for column in cursor.description]
        for raw_row in cursor.fetchall():
            row = dict(zip(columns, raw_row))
            related_id = int(row["related_knowledge_id"])
            relation_type = str(row["relation_type"])
            if related_id in visited:
                continue
            visited.add(related_id)
            next_path = path + (relation_type,)
            results.append({
                "knowledge_id": related_id,
                "depth": depth + 1,
                "relation_path": list(next_path),
                "relation_type": relation_type,
                "claim": str(row["claim"]),
                "domain_name": str(row["domain_name"]),
                "environment_name": str(row["environment_name"]),
                "environment_version": str(row["environment_version"]),
                "evidence_level": str(row["evidence_level"]),
                "lifecycle_status": str(row["lifecycle_status"]),
            })
            frontier.append((related_id, depth + 1, next_path))
            if len(results) >= limit:
                break
    return results

