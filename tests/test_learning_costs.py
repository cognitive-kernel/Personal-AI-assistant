import json

from app.learning_costs import estimate_usage_cost


def test_cost_estimate_uses_exact_model_rates_and_reported_usage():
    pricing = json.dumps({"model:tag": {"input_usd_per_million_tokens": "0.10", "output_usd_per_million_tokens": "0.20", "pricing_version": "test-1"}})
    result = estimate_usage_cost("model:tag", {"initial": {"prompt_eval_count": 1000, "eval_count": 500}}, pricing)
    assert result["status"] == "estimated"
    assert result["total_usd"] == "0.00020000"
    assert result["is_invoice"] is False


def test_cost_estimate_never_guesses_missing_price_or_usage():
    usage = {"initial": {"prompt_eval_count": 1000}}
    assert estimate_usage_cost("model", usage)["reason"] == "pricing_not_configured"
    pricing = json.dumps({"model": {"input_usd_per_million_tokens": "1", "output_usd_per_million_tokens": "2", "pricing_version": "v1"}})
    result = estimate_usage_cost("model", usage, pricing)
    assert result["status"] == "partial"
    assert result["total_usd"] is None


def test_cost_estimate_requires_exact_model_and_nonnegative_rates():
    usage = {"initial": {"prompt_eval_count": 10, "eval_count": 10}}
    assert estimate_usage_cost("model", usage, '{"model:latest": {}}')["status"] == "unknown"
    pricing = json.dumps({"model": {"input_usd_per_million_tokens": "-1", "output_usd_per_million_tokens": "2", "pricing_version": "v1"}})
    assert estimate_usage_cost("model", usage, pricing)["status"] == "unknown"
