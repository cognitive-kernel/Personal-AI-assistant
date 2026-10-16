"""Provider-neutral, dry-run resource selection from reviewed cloud evidence.

This planner produces a selection recommendation only. It never queues work,
connects to a provider, creates accounts, provisions resources, or authorizes
remote execution.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .cloud_resource_policy import assess_candidate


class ResourcePlanningError(ValueError):
    """Raised when requested resource constraints are invalid."""


def _number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResourcePlanningError(f"{name} must be a finite number.")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ResourcePlanningError(f"{name} must be a finite number.") from None
    if not math.isfinite(parsed) or parsed < minimum:
        raise ResourcePlanningError(f"{name} must be finite and at least {minimum}.")
    return parsed


def _accepted_claims(candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    claims = candidate.get("observed_claims", [])
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        return []
    return [
        claim for claim in claims
        if isinstance(claim, Mapping)
        and claim.get("is_current") == 1
        and claim.get("source_content_sha256") == candidate.get("content_sha256")
        and claim.get("review_status") == "accepted"
    ]


def _memory_gib(claim: Mapping[str, Any]) -> float | None:
    value = claim.get("value")
    unit = claim.get("unit")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not isinstance(unit, str):
        return None
    normalized = unit.strip().casefold()
    # Conservative conversion: decimal GB is slightly smaller than binary GiB.
    factors = {
        "gib": 1.0,
        "gb": 1_000_000_000 / (1024 ** 3),
        "mib": 1 / 1024,
        "mb": 1_000_000 / (1024 ** 3),
    }
    factor = factors.get(normalized)
    return number * factor if factor is not None else None


def _capabilities(claims: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cpu_values = [
        float(claim["value"])
        for claim in claims
        if claim.get("claim_type") == "cpu_count"
        and isinstance(claim.get("value"), (int, float))
        and not isinstance(claim.get("value"), bool)
        and math.isfinite(float(claim["value"]))
        and float(claim["value"]) >= 0
    ]
    memory_values = [
        parsed for claim in claims
        if claim.get("claim_type") == "memory_capacity"
        for parsed in [_memory_gib(claim)]
        if parsed is not None
    ]
    gpu_models = sorted({
        str(claim.get("value")).strip()
        for claim in claims
        if claim.get("claim_type") == "gpu_model"
        and isinstance(claim.get("value"), str)
        and claim.get("value").strip()
    })
    return {
        "cpu_count": max(cpu_values) if cpu_values else None,
        "memory_gib": max(memory_values) if memory_values else None,
        "gpu_models": gpu_models,
    }


def build_selection_plan(
    candidates: Sequence[Mapping[str, Any]],
    requirements: Mapping[str, Any],
) -> dict[str, Any]:
    """Rank provider-neutral candidates against resource needs and reviewed policy."""
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise ResourcePlanningError("candidates must be a sequence.")
    if not isinstance(requirements, Mapping):
        raise ResourcePlanningError("requirements must be an object.")

    min_cpu = _number(requirements.get("min_cpu_count", 1), "min_cpu_count", minimum=0)
    min_memory = _number(requirements.get("min_memory_gib", 0), "min_memory_gib", minimum=0)
    require_gpu = requirements.get("require_gpu", False)
    if type(require_gpu) is not bool:
        raise ResourcePlanningError("require_gpu must be a boolean.")
    requested_gpu_models = requirements.get("gpu_models", [])
    if not isinstance(requested_gpu_models, list) or any(
        not isinstance(model, str) or not model.strip() for model in requested_gpu_models
    ):
        raise ResourcePlanningError("gpu_models must be a list of non-empty strings.")
    allowed_domains = requirements.get("allowed_provider_domains", [])
    if not isinstance(allowed_domains, list) or any(
        not isinstance(domain, str) or not domain.strip() for domain in allowed_domains
    ):
        raise ResourcePlanningError("allowed_provider_domains must be a list of non-empty strings.")
    allowed = {domain.strip().casefold() for domain in allowed_domains}
    requested_models = {model.strip().casefold() for model in requested_gpu_models}

    evaluated: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ResourcePlanningError("each candidate must be an object.")
        claims = _accepted_claims(candidate)
        assessment = assess_candidate(candidate, claims)
        capabilities = _capabilities(claims)
        reasons: list[str] = []
        domain = candidate.get("provider_domain")
        if not isinstance(domain, str) or not domain.strip():
            reasons.append("Provider domain is missing.")
        elif allowed and domain.strip().casefold() not in allowed:
            reasons.append("Provider domain is outside the requested allowlist.")

        cpu = capabilities["cpu_count"]
        memory = capabilities["memory_gib"]
        if cpu is None:
            reasons.append("Current accepted CPU capacity is unknown.")
        elif cpu < min_cpu:
            reasons.append(f"CPU capacity {cpu:g} is below requested minimum {min_cpu:g}.")
        if memory is None:
            reasons.append("Current accepted memory capacity in GB/GiB is unknown.")
        elif memory < min_memory:
            reasons.append(
                f"Memory capacity {memory:.3g} GiB is below requested minimum {min_memory:.3g} GiB."
            )
        models = capabilities["gpu_models"]
        if require_gpu and not models:
            reasons.append("No current accepted GPU model claim is available.")
        elif requested_models and not requested_models.intersection(
            model.casefold() for model in models
        ):
            reasons.append("No current accepted GPU model matches the requested model allowlist.")

        if assessment["status"] != "ready_for_provider_adapter":
            reasons.append("Provider evidence/policy preflight is not ready.")
        eligible = not reasons
        evaluated.append({
            "candidate_id": candidate.get("id"),
            "provider_domain": domain,
            "eligible_for_adapter_review": eligible,
            "execution_authorized": False,
            "preflight_status": assessment["status"],
            "capabilities": capabilities,
            "reasons": reasons,
            "policy_blockers": assessment["blockers"],
            "policy_review_needed": assessment["review_needed"],
            "content_sha256": candidate.get("content_sha256"),
        })

    evaluated.sort(
        key=lambda item: (
            not item["eligible_for_adapter_review"],
            item["provider_domain"] or "",
            str(item["candidate_id"]),
        )
    )
    selected = next(
        (item for item in evaluated if item["eligible_for_adapter_review"]),
        None,
    )
    return {
        "plan_version": "provider-neutral-selection-v1",
        "status": "candidate_found" if selected else "no_eligible_candidate",
        "selected_candidate_id": selected["candidate_id"] if selected else None,
        "execution_authorized": False,
        "remote_execution_enabled": False,
        "requirements": {
            "min_cpu_count": min_cpu,
            "min_memory_gib": min_memory,
            "require_gpu": require_gpu,
            "gpu_models": sorted(requested_models),
            "allowed_provider_domains": sorted(allowed),
        },
        "candidates": evaluated,
        "limitations": [
            "Only current, accepted claims bound to the current source hash are used.",
            "Provider policy readiness is not proof of current physical capacity or zero cost.",
            "A provider-specific adapter, independent contract tests, and explicit execution authorization are still required.",
        ],
    }
