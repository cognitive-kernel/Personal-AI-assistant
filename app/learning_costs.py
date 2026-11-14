"""Explicit cost estimates require versioned rates and reported token usage."""
from __future__ import annotations
import json
from decimal import Decimal, InvalidOperation
from typing import Any


def validate_pricing_catalog_json(value: Any) -> None:
    """Validate the optional exact-model price catalog stored in Settings."""
    if not isinstance(value, str) or len(value) > 12000:
        raise ValueError("learning_model_pricing_json must be text up to 12000 characters.")
    if not value.strip():
        return
    try:
        catalog = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("learning_model_pricing_json must contain valid JSON.") from exc
    if not isinstance(catalog, dict) or len(catalog) > 500:
        raise ValueError("learning_model_pricing_json must be an object with at most 500 model entries.")
    for model_id, entry in catalog.items():
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200 or not isinstance(entry, dict):
            raise ValueError("Each pricing entry must use a valid model name and an object value.")
        version = entry.get("pricing_version")
        if not isinstance(version, str) or not version.strip() or len(version) > 100:
            raise ValueError(f"Pricing version is required for model {model_id}.")
        for rate_key in ("input_usd_per_million_tokens", "output_usd_per_million_tokens"):
            raw = entry.get(rate_key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
                raise ValueError(f"{rate_key} must be a non-negative finite number for model {model_id}.")
            try:
                rate = Decimal(str(raw))
            except InvalidOperation as exc:
                raise ValueError(f"{rate_key} is invalid for model {model_id}.") from exc
            if not rate.is_finite() or rate < 0:
                raise ValueError(f"{rate_key} must be a non-negative finite number for model {model_id}.")


def estimate_usage_cost(model_id: str, usage_by_stage: dict[str, dict[str, Any]], pricing_json: str | None = None) -> dict[str, Any]:
    """Estimate USD from explicit exact-model rates; never treat estimates as invoices."""
    if not pricing_json:
        return {"status": "unknown", "reason": "pricing_not_configured", "total_usd": None, "currency": "USD", "is_invoice": False}
    try:
        catalog = json.loads(pricing_json)
        entry = catalog[model_id]
        input_rate = Decimal(str(entry["input_usd_per_million_tokens"]))
        output_rate = Decimal(str(entry["output_usd_per_million_tokens"]))
        version = entry["pricing_version"]
        if not isinstance(version, str) or not version.strip() or not input_rate.is_finite() or not output_rate.is_finite() or input_rate < 0 or output_rate < 0:
            raise ValueError
    except (TypeError, KeyError, ValueError, InvalidOperation, json.JSONDecodeError):
        return {"status": "unknown", "reason": "pricing_missing_or_invalid", "total_usd": None, "currency": "USD", "is_invoice": False}
    stages = {}
    total = Decimal(0)
    complete = bool(usage_by_stage)
    for stage, usage in usage_by_stage.items():
        prompt = usage.get("prompt_eval_count") if isinstance(usage, dict) else None
        output = usage.get("eval_count") if isinstance(usage, dict) else None
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (prompt, output)):
            stages[stage] = {"status": "unknown", "estimated_usd": None}
            complete = False
            continue
        cost = (Decimal(prompt) * input_rate + Decimal(output) * output_rate) / Decimal(1_000_000)
        cost = cost.quantize(Decimal("0.00000001"))
        total += cost
        stages[stage] = {"status": "estimated", "input_tokens": prompt, "output_tokens": output, "estimated_usd": format(cost, "f")}
    return {"status": "estimated" if complete else "partial", "reason": None if complete else "usage_counters_missing", "currency": "USD", "model_id": model_id, "pricing_version": version, "stages": stages, "total_usd": format(total.quantize(Decimal("0.00000001")), "f") if complete else None, "is_invoice": False}
