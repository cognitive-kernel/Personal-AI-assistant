import pytest

from app.learning_pipeline_budget import LearningPipelineBudget


def test_budget_reserves_initial_generation_and_single_repair_with_shared_ceiling():
    budget = LearningPipelineBudget(max_calls=2, max_predict_tokens=2600)

    assert budget.reserve("initial_generation", predict_tokens=1200) is True
    assert budget.reserve("quality_repair", predict_tokens=1400) is True
    assert budget.reserve("third_attempt", predict_tokens=1) is False

    snapshot = budget.snapshot()
    assert snapshot["calls_used"] == 2
    assert snapshot["predict_tokens_reserved"] == 2600
    assert snapshot["budget_exhausted"] is True
    assert snapshot["billing_cost_known"] is False


def test_budget_rejects_duplicate_stage_and_does_not_charge_twice():
    budget = LearningPipelineBudget(max_calls=2, max_predict_tokens=2600)

    assert budget.reserve("initial_generation", predict_tokens=1200) is True
    assert budget.reserve("initial_generation", predict_tokens=1200) is False
    assert budget.snapshot()["calls_used"] == 1
    assert budget.snapshot()["predict_tokens_reserved"] == 1200


@pytest.mark.parametrize("kwargs", [
    {"max_calls": 0, "max_predict_tokens": 10},
    {"max_calls": 1, "max_predict_tokens": 0},
    {"max_calls": True, "max_predict_tokens": 10},
])
def test_budget_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        LearningPipelineBudget(**kwargs)


def test_budget_rejects_token_reservation_over_limit_without_mutating_state():
    budget = LearningPipelineBudget(max_calls=2, max_predict_tokens=100)

    assert budget.reserve("initial_generation", predict_tokens=101) is False
    assert budget.snapshot()["calls_used"] == 0
    assert budget.snapshot()["predict_tokens_reserved"] == 0
