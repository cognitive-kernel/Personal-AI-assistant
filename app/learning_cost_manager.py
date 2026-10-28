"""Configurable model cost/value accounting; unknown rates remain unknown, never guessed."""
from __future__ import annotations

import json
import math
from typing import Any


def _profiles(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = settings.get("learning_model_cost_profiles", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, profile in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(profile, dict):
            continue
        input_rate = profile.get("input_cost_microusd_per_1k_tokens")
        output_rate = profile.get("output_cost_microusd_per_1k_tokens")
        quality = profile.get("quality_score")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
               for value in (input_rate, output_rate)):
            continue
        if isinstance(quality, bool) or not isinstance(quality, (int, float)) or not math.isfinite(quality) or not 0 <= quality <= 1:
            continue
        energy = profile.get("energy_wh_per_1k_tokens")
        if energy is not None and (
            isinstance(energy, bool) or not isinstance(energy, (int, float))
            or not math.isfinite(energy) or energy < 0
        ):
            continue
        result[name.strip()] = {
            "input_cost_microusd_per_1k_tokens": int(input_rate),
            "output_cost_microusd_per_1k_tokens": int(output_rate),
            "quality_score": float(quality),
            "energy_wh_per_1k_tokens": float(energy) if energy is not None else None,
            "provider": str(profile.get("provider", "unspecified"))[:80],
        }
    return result


def estimate_model_usage(
    settings: dict[str, Any], model_name: str, prompt_tokens: int, completion_tokens: int
) -> dict[str, Any]:
    """Estimate cost/energy only when explicit administrator rates are configured."""
    for value, field in ((prompt_tokens, "prompt_tokens"), (completion_tokens, "completion_tokens")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer.")
    profile = _profiles(settings).get(model_name)
    if profile is None:
        return {
            "estimated_cost_microusd": None,
            "estimated_energy_wh": None,
            "cost_status": "unknown_rate",
            "provider": None,
        }
    cost = round(
        prompt_tokens * profile["input_cost_microusd_per_1k_tokens"] / 1000
        + completion_tokens * profile["output_cost_microusd_per_1k_tokens"] / 1000
    )
    energy_rate = profile["energy_wh_per_1k_tokens"]
    energy = (
        (prompt_tokens + completion_tokens) * energy_rate / 1000
        if energy_rate is not None else None
    )
    return {
        "estimated_cost_microusd": max(0, int(cost)),
        "estimated_energy_wh": round(energy, 6) if energy is not None else None,
        "cost_status": "configured_estimate",
        "provider": profile["provider"],
    }


def select_learning_model(
    settings: dict[str, Any],
    installed_model_names: list[str],
    *,
    estimated_prompt_tokens: int,
    estimated_completion_tokens: int,
    minimum_quality_score: float = 0.0,
    budget_microusd: int | None = None,
) -> dict[str, Any]:
    """Choose the cheapest configured installed model meeting the declared quality/budget."""
    if not isinstance(installed_model_names, list) or any(not isinstance(name, str) for name in installed_model_names):
        raise ValueError("installed_model_names must be a list of strings.")
    if isinstance(minimum_quality_score, bool) or not isinstance(minimum_quality_score, (int, float)) or not 0 <= minimum_quality_score <= 1:
        raise ValueError("minimum_quality_score must be between 0 and 1.")
    if budget_microusd is not None and (
        isinstance(budget_microusd, bool) or not isinstance(budget_microusd, int) or budget_microusd < 0
    ):
        raise ValueError("budget_microusd must be a non-negative integer.")
    profiles = _profiles(settings)
    candidates = []
    for name in set(installed_model_names):
        profile = profiles.get(name)
        if profile is None or profile["quality_score"] < minimum_quality_score:
            continue
        estimate = estimate_model_usage(
            settings, name, estimated_prompt_tokens, estimated_completion_tokens
        )
        cost = estimate["estimated_cost_microusd"]
        if cost is None or (budget_microusd is not None and cost > budget_microusd):
            continue
        candidates.append({
            "model": name,
            "estimated_cost_microusd": cost,
            "quality_score": profile["quality_score"],
            "provider": profile["provider"],
            "estimated_energy_wh": estimate["estimated_energy_wh"],
        })
    candidates.sort(key=lambda item: (
        item["estimated_cost_microusd"], -item["quality_score"], item["model"]
    ))
    if not candidates:
        return {
            "selected_model": None,
            "status": "no_eligible_profile",
            "reason": "No installed model has explicit cost and quality profiles meeting the configured constraints; retain the configured fallback and report cost as unknown.",
            "candidates_considered": 0,
        }
    return {
        "selected_model": candidates[0]["model"],
        "status": "selected",
        "reason": "Lowest configured estimated cost among installed models meeting the quality threshold and budget.",
        "candidates_considered": len(candidates),
        "selection": candidates[0],
    }


def learning_value_metrics(
    *,
    prior_score: float | None,
    current_score: float | None,
    estimated_cost_microusd: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    practical_successes: int = 0,
    independent_source_count: int = 0,
) -> dict[str, Any]:
    """Return transparent outcome/cost indicators without claiming causal learning gains."""
    def valid_score(value):
        return value is not None and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 100
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (prompt_tokens, completion_tokens, practical_successes, independent_source_count)):
        raise ValueError("Token and evidence counts must be non-negative integers.")
    gain = round(float(current_score) - float(prior_score), 2) if valid_score(prior_score) and valid_score(current_score) else None
    total_tokens = prompt_tokens + completion_tokens
    value_points = (
        max(0.0, gain) + min(practical_successes, 5) * 5 + min(independent_source_count, 5) * 2
        if gain is not None else None
    )
    cost_per_point = (
        round(estimated_cost_microusd / value_points, 3)
        if estimated_cost_microusd is not None and estimated_cost_microusd > 0
        and value_points is not None and value_points > 0 else None
    )
    return {
        "score_gain_points": gain,
        "total_tokens": total_tokens,
        "estimated_cost_microusd": estimated_cost_microusd,
        "provisional_value_points": round(value_points, 2) if value_points is not None else None,
        "estimated_cost_microusd_per_value_point": cost_per_point,
        "value_status": "provisional_not_causal",
        "cost_status": "known_estimate" if estimated_cost_microusd is not None else "unknown_rate",
        "note": "Value points are a transparent operational heuristic, not a causal estimate of learning. Monetary cost and energy remain unknown unless explicit rates are configured.",
    }
