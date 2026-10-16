"""Persistent, auditable experience records for the learning loop.

A record is only marked verified after a concrete test result and source evidence
are attached. This module deliberately does not equate an LLM's opinion with proof.
"""
from __future__ import annotations

import json
from typing import Any

from .db import learning_connection


STATUSES = {"identified", "researching", "ready_to_test", "testing", "verified", "failed", "needs_review"}


def init_experience_tables() -> None:
    """Ensure the versioned learning schema is initialized; do not create ad-hoc tables."""
    from .db import init_databases

    init_databases()


def _json_list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list.")
    if len(value) > 50 or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must contain at most 50 objects.")
    return value


def create_experience(topic: str, problem: str, hypothesis: str = "") -> dict[str, Any]:
    topic, problem, hypothesis = topic.strip(), problem.strip(), hypothesis.strip()
    if not topic or len(topic) > 300:
        raise ValueError("topic must contain 1–300 characters.")
    if not problem or len(problem) > 10000:
        raise ValueError("problem must contain 1–10000 characters.")
    if len(hypothesis) > 5000:
        raise ValueError("hypothesis must be at most 5000 characters.")
    init_experience_tables()
    with learning_connection() as conn:
        cur = conn.execute(
            "INSERT INTO learning_experiences(topic, problem, hypothesis) VALUES (?, ?, ?)",
            (topic, problem, hypothesis),
        )
        experience_id = int(cur.lastrowid)
    return get_experience(experience_id)


def update_experience(
    experience_id: int,
    *,
    status: str | None = None,
    source_evidence: list[dict[str, Any]] | None = None,
    test_plan: list[dict[str, Any]] | None = None,
    test_results: list[dict[str, Any]] | None = None,
    takeaway: str | None = None,
    _controlled_run: bool = False,
) -> dict[str, Any]:
    """Update a record. Verification is reserved for the internal controlled runner."""
    init_experience_tables()
    if status is not None and status not in STATUSES:
        raise ValueError("Unsupported experience status.")
    if source_evidence is not None:
        source_evidence = _json_list(source_evidence, "source_evidence")
        for item in source_evidence:
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError("Every source evidence item must include an HTTPS URL.")
            if not isinstance(item.get("claim"), str) or not item["claim"].strip():
                raise ValueError("Every source evidence item must describe the claim it supports.")
    if test_plan is not None:
        test_plan = _json_list(test_plan, "test_plan")
    if test_results is not None:
        test_results = _json_list(test_results, "test_results")
        for item in test_results:
            if item.get("passed") not in (True, False):
                raise ValueError("Every test result must have a boolean passed field.")
            if not isinstance(item.get("test"), str) or not item["test"].strip():
                raise ValueError("Every test result must name the test performed.")
    if takeaway is not None and (not isinstance(takeaway, str) or len(takeaway) > 10000):
        raise ValueError("takeaway must be at most 10000 characters.")

    with learning_connection() as conn:
        row = conn.execute("SELECT * FROM learning_experiences WHERE id=?", (experience_id,)).fetchone()
        if row is None:
            raise KeyError(experience_id)
        evidence = source_evidence if source_evidence is not None else json.loads(row["source_evidence_json"])
        results = test_results if test_results is not None else json.loads(row["test_results_json"])
        plan = test_plan if test_plan is not None else json.loads(row["test_plan_json"])
        final_status = status if status is not None else row["status"]
        # Public callers must not mutate a verified record by downgrading its status
        # in the same request. Only the controlled runner may change verified history.
        if row["status"] == "verified" and not _controlled_run:
            raise ValueError("Only the controlled experience runner can modify a verified record.")
        if final_status == "verified" and not _controlled_run:
            raise ValueError("Only the controlled experience runner can modify a verified record.")
        # A record cannot become a reusable verified experience without sources and
        # at least one successful test; failed tests must remain visible in history.
        if final_status == "verified":
            if not evidence:
                raise ValueError("Cannot verify an experience without source evidence.")
            if not plan:
                raise ValueError("Cannot verify an experience without a test plan.")
            if not results or not any(item.get("passed") is True for item in results):
                raise ValueError("Cannot verify an experience without a passing test result.")
        _store_version_snapshot(conn, row)
        conn.execute(
            """UPDATE learning_experiences SET status=?, source_evidence_json=?, test_plan_json=?,
               test_results_json=?, takeaway=?, version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (final_status, json.dumps(evidence, ensure_ascii=False),
             json.dumps(plan, ensure_ascii=False), json.dumps(results, ensure_ascii=False),
             row["takeaway"] if takeaway is None else takeaway.strip(), experience_id),
        )
    return get_experience(experience_id)


def _store_version_snapshot(conn, row) -> None:
    """Store an immutable snapshot before changing a learning experience."""
    conn.execute(
        """INSERT OR IGNORE INTO learning_experience_versions(
            experience_id, version, topic, problem, hypothesis, status,
            source_evidence_json, test_plan_json, test_results_json, takeaway)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (row["id"], row["version"], row["topic"], row["problem"], row["hypothesis"],
         row["status"], row["source_evidence_json"], row["test_plan_json"],
         row["test_results_json"], row["takeaway"]),
    )


def list_experience_versions(experience_id: int) -> list[dict[str, Any]]:
    init_experience_tables()
    with learning_connection() as conn:
        current = conn.execute("SELECT version, status, takeaway, updated_at AS created_at FROM learning_experiences WHERE id=?", (experience_id,)).fetchone()
        if current is None:
            raise KeyError(experience_id)
        rows = conn.execute(
            "SELECT version, status, takeaway, created_at FROM learning_experience_versions WHERE experience_id=? ORDER BY version DESC",
            (experience_id,),
        ).fetchall()
    versions = [dict(row) for row in rows]
    if not any(item["version"] == current["version"] for item in versions):
        versions.insert(0, dict(current))
    return versions


def rollback_experience(experience_id: int, target_version: int, *, _controlled_run: bool = False) -> dict[str, Any]:
    """Restore an older snapshot as a new version; reserved for the controlled runner."""
    if not _controlled_run:
        raise ValueError("Only the controlled experience runner can roll back an experience.")
    init_experience_tables()
    with learning_connection() as conn:
        current = conn.execute("SELECT * FROM learning_experiences WHERE id=?", (experience_id,)).fetchone()
        if current is None:
            raise KeyError(experience_id)
        target = conn.execute(
            "SELECT * FROM learning_experience_versions WHERE experience_id=? AND version=?",
            (experience_id, int(target_version)),
        ).fetchone()
        if target is None or int(target_version) >= int(current["version"]):
            raise ValueError("Target version must be an available older snapshot.")
        _store_version_snapshot(conn, current)
        conn.execute(
            """UPDATE learning_experiences SET topic=?, problem=?, hypothesis=?, status=?,
               source_evidence_json=?, test_plan_json=?, test_results_json=?, takeaway=?,
               version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (target["topic"], target["problem"], target["hypothesis"], target["status"],
             target["source_evidence_json"], target["test_plan_json"], target["test_results_json"],
             target["takeaway"], experience_id),
        )
    return get_experience(experience_id)


def get_experience(experience_id: int) -> dict[str, Any]:
    init_experience_tables()
    with learning_connection() as conn:
        row = conn.execute("SELECT * FROM learning_experiences WHERE id=?", (experience_id,)).fetchone()
    if row is None:
        raise KeyError(experience_id)
    result = dict(row)
    for column, key in (
        ("source_evidence_json", "source_evidence"),
        ("test_plan_json", "test_plan"),
        ("test_results_json", "test_results"),
    ):
        result[key] = json.loads(result.pop(column))
    return result


def list_experiences(topic: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    init_experience_tables()
    if status is not None and status not in STATUSES:
        raise ValueError("Unsupported experience status.")
    clauses, params = [], []
    if topic:
        clauses.append("topic LIKE ?")
        params.append("%" + topic.strip()[:300] + "%")
    if status:
        clauses.append("status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with learning_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM learning_experiences" + where + " ORDER BY updated_at DESC, id DESC LIMIT ?",
            (*params, max(1, min(200, int(limit)))),
        ).fetchall()
    return [get_experience(int(row["id"])) for row in rows]


def search_relevant_experiences(query: str, query_variants: list[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Return only verified experiences ranked by overlap with supplied language variants."""
    import re

    if not isinstance(query, str) or not query.strip():
        return []
    tokens: set[str] = set()
    for variant in [query, *(query_variants or [])][:8]:
        if isinstance(variant, str):
            tokens.update(re.findall(r"[\w-]{2,}", variant.casefold(), flags=re.UNICODE))
    if not tokens:
        return []
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in list_experiences(status="verified", limit=200):
        # Older chat-loop records predate claim-level review. Keep them out of reusable
        # verified context rather than silently treating provenance checks as semantic support.
        results = item.get("test_results", [])
        is_legacy_chat_record = (
            any(result.get("runner") == "chat-learning-loop-v1" for result in results)
            and not any(
                result.get("test") == "semantic_claim_review" and result.get("passed") is True
                for result in results
            )
        )
        if is_legacy_chat_record:
            continue
        searchable = " ".join([
            item["topic"], item["problem"], item["hypothesis"], item["takeaway"],
            " ".join(str(e.get("claim", "")) for e in item["source_evidence"]),
        ]).casefold()
        item_tokens = set(re.findall(r"[\w-]{2,}", searchable, flags=re.UNICODE))
        overlap = len(tokens & item_tokens)
        if overlap:
            topic_tokens = set(re.findall(r"[\w-]{2,}", item["topic"].casefold(), flags=re.UNICODE))
            score = overlap / (len(tokens) ** 0.5) + 1.5 * len(tokens & topic_tokens)
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"], pair[1]["id"]), reverse=True)
    return [item for _, item in ranked[:max(1, min(20, int(limit)))]]


def search_relevant_failures(query: str, query_variants: list[str] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """Retrieve related failed/review-needed attempts as warnings, never as verified advice."""
    import re

    if not isinstance(query, str) or not query.strip():
        return []
    tokens: set[str] = set()
    for variant in [query, *(query_variants or [])][:8]:
        if isinstance(variant, str):
            tokens.update(re.findall(r"[\w-]{2,}", variant.casefold(), flags=re.UNICODE))
    if not tokens:
        return []
    ranked: list[tuple[float, dict[str, Any]]] = []
    for status in ("needs_review", "failed"):
        for item in list_experiences(status=status, limit=200):
            searchable = " ".join([
                item["topic"], item["problem"], item["hypothesis"], item["takeaway"],
                " ".join(str(result.get("test", "")) for result in item["test_results"]),
            ]).casefold()
            item_tokens = set(re.findall(r"[\w-]{2,}", searchable, flags=re.UNICODE))
            overlap = len(tokens & item_tokens)
            if overlap:
                topic_tokens = set(re.findall(r"[\w-]{2,}", item["topic"].casefold(), flags=re.UNICODE))
                score = overlap / (len(tokens) ** 0.5) + 1.5 * len(tokens & topic_tokens)
                ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"], pair[1]["id"]), reverse=True)
    return [item for _, item in ranked[:max(1, min(10, int(limit)))]]
