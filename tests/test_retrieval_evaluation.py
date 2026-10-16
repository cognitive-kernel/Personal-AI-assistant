import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.retrieval_evaluation import (
    evaluate_ranked_queries,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k_and_reciprocal_rank():
    ranked = ["a", "b", "c", "d"]
    relevant = {"b", "d"}
    assert recall_at_k(ranked, relevant, k=2) == 0.5
    assert precision_at_k(ranked, relevant, k=2) == 0.5
    assert precision_at_k(ranked, relevant, k=4) == 0.5
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert recall_at_k(ranked, {"missing"}, k=3) == 0.0
    assert reciprocal_rank(ranked, {"missing"}) == 0.0


@pytest.mark.parametrize("k", [0, -1, True, 1.5, "5"])
def test_retrieval_metrics_reject_invalid_k(k):
    with pytest.raises(ValueError, match="positive integer"):
        recall_at_k(["a"], {"a"}, k=k)


def test_evaluate_ranked_queries_reports_recall_and_mrr():
    cases = [
        {"id": "q1", "language": "en", "query": "first", "query_variants": [], "relevant_slugs": ["a"]},
        {"id": "q2", "language": "fa", "query": "second", "query_variants": ["دو"], "relevant_slugs": ["c"]},
    ]

    def fake_search(query, variants, limit):
        assert limit == 10
        if query == "first":
            return [{"slug": "b"}, {"slug": "a"}]
        return [{"slug": "c"}]

    result = evaluate_ranked_queries(
        cases, fake_search, identifier=lambda item: item["slug"], k=5
    )
    assert result["query_count"] == 2
    assert result["recall_at_5"] == 1.0
    assert result["precision_at_5"] == 0.3
    assert result["mrr"] == 0.75
    assert result["per_query"][0]["top_results"] == ["b", "a"]


def test_bilingual_benchmark_fixture_has_valid_relevance_targets():
    path = Path(__file__).parent / "fixtures" / "experience_retrieval_eval.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    slugs = {item["slug"] for item in dataset["experiences"]}
    assert len(dataset["experiences"]) >= 10
    assert len(dataset["queries"]) >= 15
    assert {"fa", "en"} <= {case["language"] for case in dataset["queries"]}
    assert all(case["relevant_slugs"] and set(case["relevant_slugs"]) <= slugs for case in dataset["queries"])
    assert all(case["query_variants"] for case in dataset["queries"])
    assert any(len(case["relevant_slugs"]) > 1 for case in dataset["queries"])
    assert {"en_lease_duplicate", "fa_lease_recovery", "fa_proxy_isolation", "en_model_response_limit"} <= {
        case["id"] for case in dataset["queries"]
    }



def test_lexical_benchmark_runs_in_an_isolated_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "evaluate_experience_retrieval.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--lexical-only"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    report = json.loads(completed.stdout)
    dataset = json.loads((root / "tests" / "fixtures" / "experience_retrieval_eval.json").read_text(encoding="utf-8"))
    assert report["query_count"] == len(dataset["queries"])
    assert 0.0 <= report["recall_at_5"] <= 1.0
    assert 0.0 <= report["precision_at_5"] <= 1.0
    assert 0.0 <= report["mrr"] <= 1.0
    assert report["dataset_type"] == "synthetic_bilingual"
    assert report["retrieval_mode"] == "lexical_fallback_no_vectors_cached"
    assert len(report["per_query"]) == len(dataset["queries"])
