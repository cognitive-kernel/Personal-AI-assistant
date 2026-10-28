import pytest

from app import learning_cost_manager


def test_cost_estimate_is_unknown_without_explicit_rates():
    result = learning_cost_manager.estimate_model_usage(
        {}, "local-model", prompt_tokens=1000, completion_tokens=500
    )
    assert result["estimated_cost_microusd"] is None
    assert result["estimated_energy_wh"] is None
    assert result["cost_status"] == "unknown_rate"


def test_cost_and_energy_estimates_use_only_configured_rates():
    settings = {
        "learning_model_cost_profiles": {
            "provider-model": {
                "provider": "configured-provider",
                "input_cost_microusd_per_1k_tokens": 1000,
                "output_cost_microusd_per_1k_tokens": 2000,
                "quality_score": 0.9,
                "energy_wh_per_1k_tokens": 0.5,
            }
        }
    }
    result = learning_cost_manager.estimate_model_usage(
        settings, "provider-model", prompt_tokens=1500, completion_tokens=500
    )
    assert result["estimated_cost_microusd"] == 2500
    assert result["estimated_energy_wh"] == 1.0
    assert result["cost_status"] == "configured_estimate"


def test_model_selection_respects_quality_budget_and_installed_models():
    settings = {
        "learning_model_cost_profiles": {
            "cheap-low-quality": {
                "input_cost_microusd_per_1k_tokens": 100,
                "output_cost_microusd_per_1k_tokens": 100,
                "quality_score": 0.4,
            },
            "mid": {
                "input_cost_microusd_per_1k_tokens": 500,
                "output_cost_microusd_per_1k_tokens": 700,
                "quality_score": 0.85,
            },
            "expensive": {
                "input_cost_microusd_per_1k_tokens": 1000,
                "output_cost_microusd_per_1k_tokens": 1000,
                "quality_score": 0.99,
            },
        }
    }
    result = learning_cost_manager.select_learning_model(
        settings, ["cheap-low-quality", "mid", "expensive", "unprofiled"],
        estimated_prompt_tokens=1000, estimated_completion_tokens=1000,
        minimum_quality_score=0.8, budget_microusd=1500,
    )
    assert result["selected_model"] == "mid"
    assert result["selection"]["estimated_cost_microusd"] == 1200


def test_model_selection_fails_closed_when_no_profile_meets_budget():
    settings = {
        "learning_model_cost_profiles": {
            "too-expensive": {
                "input_cost_microusd_per_1k_tokens": 1000,
                "output_cost_microusd_per_1k_tokens": 1000,
                "quality_score": 0.9,
            }
        }
    }
    result = learning_cost_manager.select_learning_model(
        settings, ["too-expensive"], estimated_prompt_tokens=1000,
        estimated_completion_tokens=1000, budget_microusd=10,
    )
    assert result["selected_model"] is None
    assert result["status"] == "no_eligible_profile"


def test_value_metrics_do_not_claim_causality_or_invent_unknown_cost():
    result = learning_cost_manager.learning_value_metrics(
        prior_score=50, current_score=75, estimated_cost_microusd=None,
        prompt_tokens=1200, completion_tokens=300, practical_successes=2,
        independent_source_count=2,
    )
    assert result["score_gain_points"] == 25
    assert result["total_tokens"] == 1500
    assert result["estimated_cost_microusd_per_value_point"] is None
    assert result["cost_status"] == "unknown_rate"
    assert result["value_status"] == "provisional_not_causal"


@pytest.mark.parametrize("bad_rate", [-1, True, float("nan"), "unknown"])
def test_invalid_cost_profiles_are_not_used(bad_rate):
    settings = {
        "learning_model_cost_profiles": {
            "bad-model": {
                "input_cost_microusd_per_1k_tokens": bad_rate,
                "output_cost_microusd_per_1k_tokens": 1,
                "quality_score": 0.9,
            }
        }
    }
    result = learning_cost_manager.estimate_model_usage(
        settings, "bad-model", prompt_tokens=10, completion_tokens=10
    )
    assert result["estimated_cost_microusd"] is None
    assert result["cost_status"] == "unknown_rate"
