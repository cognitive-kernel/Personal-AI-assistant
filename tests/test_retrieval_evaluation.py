import pytest

from app.retrieval_evaluation import evaluate_cases, recall_at_k, reciprocal_rank


def test_recall_at_k_and_reciprocal_rank():
    relevant = {"b", "d"}
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(relevant, ranked, 3) == 0.5
    assert reciprocal_rank(relevant, ranked) == 0.5


def test_retrieval_metrics_validate_k():
    with pytest.raises(ValueError, match="at least 1"):
        recall_at_k({"a"}, ["a"], 0)
    with pytest.raises(ValueError, match="at least 1"):
        evaluate_cases([], lambda _query, _k: [], k=0)


def test_evaluate_cases_aggregates_per_query_metrics():
    cases = [
        {"query": "first", "relevant_ids": ["b"]},
        {"query": "second", "relevant_ids": ["z"]},
    ]

    def searcher(query, _k):
        return [{"url": "b"}, {"url": "a"}] if query == "first" else [{"url": "x"}]

    result = evaluate_cases(cases, searcher, k=2)
    assert result["queries_evaluated"] == 2
    assert result["mean_recall_at_k"] == 0.5
    assert result["mrr"] == 0.5
    assert result["results"][0]["retrieved_ids"] == ["b", "a"]


def test_evaluate_cases_rejects_empty_dataset():
    with pytest.raises(ValueError, match="at least one case"):
        evaluate_cases([], lambda _query, _k: [])


@pytest.mark.parametrize("relevant_ids", [[], [""], ["   "], [None], [1]])
def test_evaluate_cases_rejects_empty_or_non_string_relevance_ids(relevant_ids):
    with pytest.raises(ValueError, match="relevant_ids"):
        evaluate_cases(
            [{"query": "query", "relevant_ids": relevant_ids}],
            lambda _query, _k: [],
        )


def test_evaluate_cases_rejects_missing_relevance_labels():
    with pytest.raises(ValueError, match="relevant_ids"):
        evaluate_cases([{"query": "no labels"}], lambda _query, _k: [])
