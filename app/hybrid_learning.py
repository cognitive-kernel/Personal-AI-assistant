"""Unified hybrid-learning orchestration.

Each verification channel is attempted independently. Failures are persisted and
never discard the temporary candidate. No channel opinion alone promotes knowledge.
"""
from __future__ import annotations

import os
from typing import Any

from . import db


def run_hybrid_verification(
    candidate_id: int, *, query: str | None = None,
    web_mode: str = "search", run_sandbox: bool = True,
    auto_correct: bool | None = None,
) -> dict[str, Any]:
    """Run web research, configured peer-AI review, and scoped sandbox tests.

    web_mode="skip_requested_url_failed" prevents unrelated discovery when the
    user-supplied URL failed. A channel failure never suppresses the other channels.
    """
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if web_mode not in {"search", "disabled", "skip_requested_url_failed"}:
        raise ValueError("Unsupported web research mode.")
    if auto_correct is None:
        auto_correct = os.environ.get("LEARNING_AUTO_SELF_CORRECTION", "true").strip().lower() not in {
            "0", "false", "no", "off",
        }
    db.init_databases()
    with db.learning_connection() as conn:
        candidate = conn.execute(
            """SELECT c.id, c.topic, c.content, c.source_urls_json, c.content_sha256
               FROM temporary_knowledge_candidates c WHERE c.id=?""",
            (candidate_id,),
        ).fetchone()
    if candidate is None:
        raise KeyError(candidate_id)

    topic, content = str(candidate["topic"] or ""), str(candidate["content"] or "")
    research_query = (query or f"{topic} {content[:300]}").strip()[:1000] or topic[:1000]
    channels: dict[str, dict[str, Any]] = {}
    from .knowledge_review import (
        consult_configured_peer_ais, get_candidate_review_summary,
        record_review_attempt, research_candidate_online,
    )

    if web_mode == "skip_requested_url_failed":
        record_review_attempt(candidate_id, channel="web_research", status="unavailable",
            details={"reason": "explicit_requested_url_failed_no_automatic_substitution"})
        channels["web_research"] = {"status": "unavailable",
            "reason": "explicit_requested_url_failed_no_automatic_substitution"}
    elif web_mode == "disabled" or os.environ.get("LEARNING_AUTO_WEB_RESEARCH", "true").strip().lower() in {"0", "false", "no", "off"}:
        record_review_attempt(candidate_id, channel="web_research", status="unconfigured",
            details={"reason": "automatic_web_research_disabled"})
        channels["web_research"] = {"status": "unconfigured"}
    else:
        try:
            channels["web_research"] = research_candidate_online(candidate_id, research_query, limit=3)
        except Exception as exc:
            record_review_attempt(candidate_id, channel="web_research", status="failed",
                details={"error_type": type(exc).__name__})
            channels["web_research"] = {"status": "failed", "error_type": type(exc).__name__}

    try:
        channels["peer_ai"] = consult_configured_peer_ais(candidate_id, limit=3)
    except Exception as exc:
        record_review_attempt(candidate_id, channel="peer_ai", status="failed",
            details={"error_type": type(exc).__name__})
        channels["peer_ai"] = {"status": "failed", "error_type": type(exc).__name__}

    try:
        from .learning_consensus import evaluate_candidate_consensus
        channels["consensus"] = evaluate_candidate_consensus(candidate_id)
    except Exception as exc:
        record_review_attempt(candidate_id, channel="peer_ai", status="failed",
            details={"reason": "consensus_evaluation_failed", "error_type": type(exc).__name__})
        channels["consensus"] = {"outcome": "insufficient_evidence", "error_type": type(exc).__name__}

    if run_sandbox:
        try:
            from .learning_verification import run_candidate_source_doctests
            channels["sandbox"] = run_candidate_source_doctests(candidate_id)
        except Exception as exc:
            record_review_attempt(candidate_id, channel="sandbox", status="failed",
                details={"error_type": type(exc).__name__})
            channels["sandbox"] = {"status": "failed", "error_type": type(exc).__name__, "promoted": False}
    else:
        record_review_attempt(candidate_id, channel="sandbox", status="unconfigured",
            details={"reason": "sandbox_channel_disabled_by_caller"})
        channels["sandbox"] = {"status": "unconfigured", "promoted": False}

    evidence_only_promotion: dict[str, Any] = {"promoted": False, "reason": "not_attempted"}
    try:
        from .knowledge_review import promote_candidate_after_evidence_review
        evidence_only_promotion = promote_candidate_after_evidence_review(candidate_id)
    except Exception as exc:
        # This is a promotion-policy evaluation error, not a peer-AI channel failure.
        evidence_only_promotion = {
            "promoted": False, "reason": type(exc).__name__,
            "details": "Evidence-only promotion could not be evaluated; candidate remains available for review.",
        }

    try:
        review = get_candidate_review_summary(candidate_id)
    except Exception as exc:
        review = {"candidate_id": candidate_id, "summary_error": type(exc).__name__}
    try:
        from .knowledge_review import build_claim_verification_matrix
        matrix = build_claim_verification_matrix(candidate_id)
    except Exception:
        matrix = None

    automatic_correction: dict[str, Any] = {"status": "not_needed"}
    if auto_correct:
        with db.learning_connection() as conn:
            persisted_state = conn.execute(
                """SELECT status, trust_level, learning_status
                   FROM temporary_knowledge_candidates WHERE id=?""",
                (candidate_id,),
            ).fetchone()
        should_correct = bool(
            persisted_state
            and (
                str(persisted_state["trust_level"]) == "L-1"
                or str(persisted_state["learning_status"]) in {"failed", "conflicting"}
                or str(persisted_state["status"]) in {"failed", "conflicted"}
            )
        )
        if should_correct:
            try:
                from .learning_consensus import start_self_correction
                automatic_correction = start_self_correction(candidate_id)
                automatic_correction["trigger"] = "persisted_failure_or_conflict"
            except ValueError as exc:
                automatic_correction = {"status": "not_started", "reason": str(exc)}
            except Exception as exc:
                automatic_correction = {"status": "failed", "error_type": type(exc).__name__}
                try:
                    record_review_attempt(
                        candidate_id, channel="peer_ai", status="failed",
                        details={"reason": "automatic_self_correction_failed", "error_type": type(exc).__name__},
                    )
                except Exception:
                    pass

    return {
        "candidate_id": candidate_id, "candidate_sha256": candidate["content_sha256"],
        "channels": {key: value for key, value in channels.items() if key in {"web_research", "peer_ai", "sandbox"}},
        "consensus": channels.get("consensus"),
        "automatic_self_correction": automatic_correction,
        "review": review, "claim_matrix": matrix,
        "evidence_only_promotion": evidence_only_promotion,
        "promotion_performed": bool(evidence_only_promotion.get("promoted") or channels.get("sandbox", {}).get("promoted")),
        "policy": (
            "A passing trusted sandbox test promotes only within its declared test scope. "
            "Without a trusted passing test suite, evidence and peer-AI reviews remain advisory "
            "and the candidate stays in temporary storage; they never promote durable knowledge."
        ),
    }
