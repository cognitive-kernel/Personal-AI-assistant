"""Conservative preflight policy for cloud-resource candidates.

This module assesses evidence readiness only. It never authorizes or launches
remote work; provider-specific execution adapters remain out of scope.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

_CAPACITY_CLAIMS = {"cpu_count", "memory_capacity", "storage_capacity", "gpu_model"}
_LIMIT_CLAIMS = {"usage_quota", "monthly_compute_hours", "session_limit"}
_ALLOWED_AUTH_METHODS = {
    "anonymous",
    "user_provided_credential",
    "provider_oauth_user_consent",
}


def _positive_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def assess_candidate(
    candidate: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return explicit blockers and missing review fields; never authorize execution."""
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
    try:
        parsed_terms = urlparse(terms_url) if isinstance(terms_url, str) else None
        terms_port = parsed_terms.port if parsed_terms else None
        terms_valid = bool(
            parsed_terms
            and parsed_terms.scheme == "https"
            and parsed_terms.hostname
            and not parsed_terms.username
            and not parsed_terms.password
            and terms_port in {None, 443}
        )
    except ValueError:
        terms_valid = False
    if not terms_valid:
        blockers.append("A reviewed public HTTPS terms URL is missing or invalid.")

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
    if isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)) or not math.isfinite(max_cost):
        review_needed.append("A finite numeric max_cost_usd limit is required.")
    elif max_cost != 0:
        blockers.append("The current policy allows zero spend only; max_cost_usd must be 0.")

    positive_price_claims = [
        claim for claim in accepted
        if claim.get("claim_type") == "price_rate"
        and isinstance(claim.get("value"), (int, float))
        and not isinstance(claim.get("value"), bool)
        and math.isfinite(claim["value"])
        and claim["value"] > 0
    ]
    if positive_price_claims:
        blockers.append("A positive price-rate claim is accepted; zero-cost use is not established.")

    auth_method = quota.get("auth_method")
    if auth_method is None:
        review_needed.append("An explicit supported authentication method must be reviewed.")
    elif auth_method not in _ALLOWED_AUTH_METHODS:
        blockers.append(
            "Authentication method is unsupported; automated account creation, account rotation, "
            "and quota circumvention are not permitted."
        )

    selected_region = quota.get("selected_region")
    allowed_regions = quota.get("allowed_regions")
    if not isinstance(selected_region, str) or not selected_region.strip():
        review_needed.append("A concrete execution region must be selected.")
    else:
        normalized_region = selected_region.strip().casefold()
        if isinstance(allowed_regions, list) and allowed_regions:
            normalized_allowed = {
                item.strip().casefold()
                for item in allowed_regions
                if isinstance(item, str) and item.strip()
            }
            if normalized_region not in normalized_allowed:
                blockers.append("Selected region is not in the reviewed provider region allowlist.")
        accepted_regions = {
            str(claim.get("value", "")).strip().casefold()
            for claim in accepted
            if claim.get("claim_type") == "region"
        }
        if normalized_region not in accepted_regions:
            review_needed.append("The selected region is not supported by a current accepted region claim.")
    if not isinstance(allowed_regions, list) or not allowed_regions:
        review_needed.append("A reviewed provider region allowlist is required.")

    max_session_minutes = quota.get("max_session_minutes")
    if not _positive_finite_number(max_session_minutes):
        review_needed.append("A positive local max_session_minutes safety cap is required.")

    max_monthly_hours = quota.get("max_monthly_hours")
    if not _positive_finite_number(max_monthly_hours):
        review_needed.append("A positive local max_monthly_hours safety cap is required.")

    if blockers:
        readiness = "blocked"
    elif review_needed:
        readiness = "needs_review"
    else:
        readiness = "ready_for_provider_adapter"
        review_needed.append(
            "Evidence and local limits are reviewed, but a provider-specific adapter must still "
            "enforce terms, quotas, authentication, region, and cost limits."
        )

    return {
        "status": readiness,
        "execution_authorized": False,
        "blockers": blockers,
        "review_needed": review_needed,
        "accepted_claim_count": len(accepted),
        "policy_version": "cloud-resource-preflight-v2",
    }
