"""Conservative preflight policy for cloud-resource candidates.

This module assesses evidence readiness only. It never authorizes or launches
remote work; provider-specific enforcement adapters are still required.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

_CAPACITY_CLAIMS = {"cpu_count", "memory_capacity", "storage_capacity", "gpu_model"}
_LIMIT_CLAIMS = {"usage_quota", "monthly_compute_hours", "session_limit"}


def assess_candidate(
    candidate: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return explicit blockers and readiness without ever authorizing execution."""
    blockers: list[str] = []
    review_needed: list[str] = []

    if candidate.get("review_status") == "rejected":
        blockers.append("Source was explicitly rejected.")
    elif (
        candidate.get("review_status") != "approved"
        or candidate.get("reviewed_content_sha256") != candidate.get("content_sha256")
    ):
        blockers.append("Current source content has not been approved.")

    if candidate.get("freshness_status") != "fresh":
        blockers.append("Source evidence is stale or freshness is unknown.")

    if candidate.get("availability_status") != "available":
        blockers.append("Provider availability has not been confirmed as available.")

    terms_url = candidate.get("terms_url")
    parsed_terms = urlparse(terms_url) if isinstance(terms_url, str) else None
    if not parsed_terms or parsed_terms.scheme != "https" or not parsed_terms.hostname:
        blockers.append("A reviewed HTTPS terms URL is missing.")

    current_hash = candidate.get("content_sha256")
    accepted = [
        claim for claim in claims
        if claim.get("is_current") == 1
        and claim.get("source_content_sha256") == current_hash
        and claim.get("review_status") == "accepted"
    ]
    accepted_types = {claim.get("claim_type") for claim in accepted}
    if not accepted_types.intersection(_CAPACITY_CLAIMS):
        review_needed.append("No current capacity claim has been accepted.")
    if not accepted_types.intersection(_LIMIT_CLAIMS):
        review_needed.append("No current quota or runtime-limit claim has been accepted.")
    if "free_tier_mention" not in accepted_types:
        review_needed.append("No explicit free-tier claim has been accepted.")

    quota = candidate.get("quota")
    if not isinstance(quota, Mapping):
        quota = {}
    max_cost = quota.get("max_cost_usd")
    if isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)):
        review_needed.append("A numeric max_cost_usd limit is required.")
    elif max_cost != 0:
        blockers.append("The current policy allows zero spend only; max_cost_usd must be 0.")

    positive_price_claims = [
        claim for claim in accepted
        if claim.get("claim_type") == "price_rate"
        and isinstance(claim.get("value"), (int, float))
        and not isinstance(claim.get("value"), bool)
        and claim["value"] > 0
    ]
    if positive_price_claims:
        blockers.append("A positive price-rate claim is accepted; zero-cost use is not established.")

    if blockers:
        readiness = "blocked"
    elif review_needed:
        readiness = "needs_review"
    else:
        readiness = "ready_for_provider_adapter"
        review_needed.append(
            "Evidence is reviewed, but a provider-specific adapter must still enforce terms, quotas, authentication, and cost limits."
        )

    return {
        "status": readiness,
        "execution_authorized": False,
        "blockers": blockers,
        "review_needed": review_needed,
        "accepted_claim_count": len(accepted),
        "policy_version": "cloud-resource-preflight-v1",
    }
