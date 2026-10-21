"""Versioned trust-level transitions for the roadmap's multi-model learning loop.

Model agreement is a necessary but insufficient signal. This module records each
consensus decision separately from raw peer responses and never calls it proof of truth.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import init_databases, learning_connection

CONSENSUS_PROMPT_VERSION = "learning-consensus-v1"
_ALLOWED_STANCES = {"supports", "contradicts", "mixed", "insufficient_evidence"}


def evaluate_candidate_consensus(candidate_id: int, *, minimum_models: int = 2) -> dict[str, Any]:
    """Evaluate latest distinct model votes and persist L1, L-1, or insufficient evidence.

    To count as L1, the exact candidate hash must still match, a source record must
    exist, and at least two distinct provider/model pairs must explicitly support it.
    Any explicit contradiction is fail-closed as conflicting; a vote is not truth.
    """
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if isinstance(minimum_models, bool) or not isinstance(minimum_models, int) or not 2 <= minimum_models <= 3:
        raise ValueError("minimum_models must be 2 or 3.")

    init_databases()
    with learning_connection() as conn:
        candidate = conn.execute(
            """SELECT id, job_id, topic, content, source_kind, source_urls_json,
                      content_sha256, promoted_knowledge_id, trust_level, learning_status
               FROM temporary_knowledge_candidates WHERE id=?""",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise KeyError(candidate_id)
        peer_rows = conn.execute(
            """SELECT id, provider, model_name, stance, response, response_sha256,
                      independence_note, created_at
               FROM knowledge_peer_consultations WHERE candidate_id=? ORDER BY id DESC""",
            (candidate_id,),
        ).fetchall()
        source_rows = conn.execute(
            """SELECT source_url, evidence_kind, excerpt
               FROM knowledge_review_evidence WHERE candidate_id=? ORDER BY id DESC""",
            (candidate_id,),
        ).fetchall()
        source_text_row = conn.execute(
            "SELECT content FROM learning_source_text WHERE job_id=?",
            (candidate["job_id"],),
        ).fetchone()

    actual_digest = hashlib.sha256(str(candidate["content"] or "").encode("utf-8")).hexdigest()
    hash_matches = actual_digest == str(candidate["content_sha256"] or "")
    try:
        candidate_urls = json.loads(candidate["source_urls_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        candidate_urls = []
    url_sources = sorted({
        str(url).strip() for url in candidate_urls
        if isinstance(url, str) and url.strip().startswith(("https://", "http://"))
    })
    url_sources.extend(
        str(row["source_url"]).strip() for row in source_rows
        if isinstance(row["source_url"], str) and row["source_url"].strip()
    )
    source_text = str(source_text_row["content"] or "") if source_text_row else ""
    source_present = bool(url_sources or (source_text.strip() and candidate["source_kind"] != "model_knowledge_no_external_source"))

    # Keep the latest response for each distinct provider/model pair. Repeated runs
    # from one model must never be counted as independent votes.
    unique_votes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in peer_rows:
        provider = str(row["provider"] or "").strip()
        model = str(row["model_name"] or "").strip()
        if not provider or not model:
            continue
        identity = (provider.casefold(), model.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        stance = str(row["stance"] or "mixed")
        if stance not in _ALLOWED_STANCES:
            stance = "mixed"
        response = str(row["response"] or "")
        unique_votes.append({
            "consultation_id": int(row["id"]),
            "provider": provider,
            "model_name": model,
            "vote": stance,
            "response_sha256": str(row["response_sha256"] or ""),
            "response_excerpt": response[:1200],
            "independence_note": str(row["independence_note"] or "")[:1000],
            "created_at": str(row["created_at"]),
        })

    clear_votes = [item for item in unique_votes if item["vote"] in {"supports", "contradicts"}]
    supporting = sum(item["vote"] == "supports" for item in clear_votes)
    contradicting = sum(item["vote"] == "contradicts" for item in clear_votes)
    uncertain = len(unique_votes) - supporting - contradicting
    score = round(supporting / len(clear_votes), 4) if clear_votes else 0.0

    if not hash_matches:
        outcome = "conflicting"
        decision_reason = "candidate_content_hash_mismatch"
    elif contradicting:
        outcome = "conflicting"
        decision_reason = "one_or_more_explicit_contradicting_votes"
    elif source_present and supporting >= minimum_models:
        outcome = "model_consensus"
        decision_reason = "minimum_distinct_supporting_models_and_source_record_present"
    else:
        outcome = "insufficient_evidence"
        blockers = []
        if not source_present:
            blockers.append("original_source_missing")
        if supporting < minimum_models:
            blockers.append("minimum_distinct_supporting_models_not_reached")
        decision_reason = ",".join(blockers) or "insufficient_evidence"

    models_used = [
        {"consultation_id": item["consultation_id"], "provider": item["provider"],
         "model_name": item["model_name"], "response_sha256": item["response_sha256"]}
        for item in unique_votes
    ]
    votes_json = json.dumps(unique_votes, ensure_ascii=False, sort_keys=True, allow_nan=False)
    models_json = json.dumps(models_used, ensure_ascii=False, sort_keys=True, allow_nan=False)

    with learning_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO knowledge_consensus_records(
                   candidate_id, candidate_sha256, prompt_version, models_used_json,
                   votes_json, supporting_count, contradicting_count, uncertain_count,
                   consensus_score, source_present, outcome
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, actual_digest, CONSENSUS_PROMPT_VERSION, models_json, votes_json,
             supporting, contradicting, uncertain, score, int(source_present), outcome),
        )
        consensus_id = int(cursor.lastrowid)
        current = conn.execute(
            """SELECT trust_level, learning_status, promoted_knowledge_id
               FROM temporary_knowledge_candidates WHERE id=?""",
            (candidate_id,),
        ).fetchone()
        if outcome == "model_consensus":
            conn.execute(
                """UPDATE temporary_knowledge_candidates
                   SET trust_level='L1', learning_status='model_consensus',
                       updated_at=CURRENT_TIMESTAMP WHERE id=? AND status!='failed'""",
                (candidate_id,),
            )
        elif outcome == "conflicting":
            conn.execute(
                """UPDATE temporary_knowledge_candidates
                   SET trust_level='L-1', learning_status='conflicting',
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (candidate_id,),
            )
            if current["promoted_knowledge_id"] is not None:
                conn.execute(
                    """UPDATE knowledge_items
                       SET trust_level='L-1', learning_status='conflicting', status='conflicted'
                       WHERE id=?""",
                    (current["promoted_knowledge_id"],),
                )
            conn.execute(
                """INSERT INTO learning_experiences(
                       topic, problem, hypothesis, status, source_evidence_json,
                       test_plan_json, test_results_json, takeaway
                   ) VALUES (?, ?, ?, 'failed', ?, '[]', ?, ?)""",
                (
                    str(candidate["topic"]),
                    f"اجماع متناقض برای نامزد دانش {candidate_id} (record {consensus_id})",
                    "ادعای فعلی بدون رسیدگی به رأی مخالف قابل استفادهٔ عادی نیست.",
                    json.dumps([{"source": url} for url in url_sources[:20]], ensure_ascii=False),
                    votes_json[:20000],
                    "یک یا چند مدل با ادعا مخالفت کردند؛ پاسخ‌های خام در knowledge_peer_consultations نگهداری شده‌اند.",
                ),
            )
        result = {
            "id": consensus_id, "candidate_id": candidate_id, "candidate_sha256": actual_digest,
            "hash_matches": hash_matches, "prompt_version": CONSENSUS_PROMPT_VERSION,
            "minimum_models": minimum_models, "models_used": models_used, "votes": unique_votes,
            "supporting_count": supporting, "contradicting_count": contradicting,
            "uncertain_count": uncertain, "consensus_score": score, "source_present": source_present,
            "outcome": outcome, "decision_reason": decision_reason,
            "trust_level": "L1" if outcome == "model_consensus" else "L-1" if outcome == "conflicting" else str(current["trust_level"]),
            "learning_status": outcome if outcome in {"model_consensus", "conflicting"} else str(current["learning_status"]),
            "consensus_is_not_proof_of_truth": True,
        }
    return result


def latest_candidate_consensus(candidate_id: int) -> dict[str, Any] | None:
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    init_databases()
    with learning_connection() as conn:
        row = conn.execute(
            """SELECT * FROM knowledge_consensus_records
               WHERE candidate_id=? ORDER BY id DESC LIMIT 1""",
            (candidate_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["models_used"] = json.loads(item.pop("models_used_json") or "[]")
    item["votes"] = json.loads(item.pop("votes_json") or "[]")
    item["consensus_is_not_proof_of_truth"] = True
    return item
