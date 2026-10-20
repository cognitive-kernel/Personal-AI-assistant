import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_experience_retrieval.py"
SPEC = importlib.util.spec_from_file_location("evaluate_experience_retrieval", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(evaluation)


def test_retrieval_acceptance_passes_only_when_all_thresholds_pass():
    metrics = {
        "recall_at_5": 0.8,
        "mrr": 0.7,
        "per_query": [
            {"id": "a", "recall_at_k": 1.0},
            {"id": "b", "recall_at_k": 0.5},
            {"id": "c", "recall_at_k": 1.0},
        ],
    }

    result = evaluation.apply_acceptance_thresholds(metrics)

    assert result["acceptance"]["passed"] is True
    assert result["zero_recall_query_rate"] == 0.0


def test_retrieval_acceptance_fails_closed_on_low_metrics_and_empty_queries():
    result = evaluation.apply_acceptance_thresholds({
        "recall_at_5": 0.4,
        "mrr": 0.3,
        "per_query": [
            {"id": "a", "recall_at_k": 0.0},
            {"id": "b", "recall_at_k": 0.5},
        ],
    })

    assert result["acceptance"]["passed"] is False
    assert result["acceptance"]["gates"] == {
        "minimum_recall_at_k": False,
        "minimum_mean_reciprocal_rank": False,
        "maximum_zero_recall_query_rate": False,
    }
    invalid = evaluation.apply_acceptance_thresholds({"recall_at_5": 1.0, "mrr": 1.0, "per_query": []})
    assert invalid["acceptance"]["status"] == "invalid_metrics"


def test_retrieval_acceptance_rejects_invalid_thresholds():
    import pytest

    with pytest.raises(ValueError):
        evaluation.apply_acceptance_thresholds(
            {"recall_at_5": 1.0, "mrr": 1.0, "per_query": [{"recall_at_k": 1.0}]},
            min_mrr=1.5,
        )
