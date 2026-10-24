"""Explicit cost estimates require versioned rates and reported token usage."""
from __future__ import annotations
import json
from decimal import Decimal, InvalidOperation
from typing import Any


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
