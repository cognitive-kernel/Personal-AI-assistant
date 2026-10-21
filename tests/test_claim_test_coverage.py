import importlib.util
from pathlib import Path

import pytest

from app.claim_test_coverage import (
    CLAIM_TEST_COVERAGE_VERSION,
    evaluate_claim_test_coverage,
    extract_candidate_claims,
)


def test_claim_ids_are_stable_and_match_sentence_boundaries():
    content = "First claim with enough meaningful words to be tracked.\nSecond claim also long enough for the review matrix."
    assert extract_candidate_claims(content, 7) == [
        {"claim_id": "7:1", "text": "First claim with enough meaningful words to be tracked."},
        {"claim_id": "7:2", "text": "Second claim also long enough for the review matrix."},
    ]


def test_each_claim_must_have_an_explicit_test_mapping():
    report = evaluate_claim_test_coverage(
        ["C1", "C2"],
        [{"test": "check-first", "claim_ids": ["C1"]}],
    )
    assert report["version"] == CLAIM_TEST_COVERAGE_VERSION
    assert report["complete"] is False
    assert report["missing_claim_ids"] == ["C2"]
    assert "one_or_more_claims_have_no_tests" in report["issues"]


def test_complete_mapping_accepts_known_claims_and_reports_nonsemantic_limit():
    report = evaluate_claim_test_coverage(
        ["C1", "C2"],
        [
            {"test": "check-first", "claim_ids": ["C1"]},
            {"test": "check-second", "claim_ids": ["C2"]},
        ],
    )
    assert report["complete"] is True
    assert report["coverage_ratio"] == 1.0
    assert report["claim_to_tests"] == {"C1": ["check-first"], "C2": ["check-second"]}
    assert report["semantic_test_adequacy_independently_verified"] is False


@pytest.mark.parametrize(
    "claim_ids,plan",
    [
        ([], [{"test": "t", "claim_ids": ["C1"]}]),
        (["C1"], []),
        (["C1"], [{"test": "t"}]),
        (["C1"], [{"test": "t", "claim_ids": ["UNKNOWN"]}]),
        (["C1", "C2"], [{"test": "same", "claim_ids": ["C1"]}, {"test": "same", "claim_ids": ["C2"]}]),
        (["C1"], [{"test": "t", "claim_ids": ["C1", "C1"]}]),
    ],
)
def test_incomplete_or_ambiguous_mapping_fails_closed(claim_ids, plan):
    assert evaluate_claim_test_coverage(claim_ids, plan)["complete"] is False


def test_versioned_multidomain_benchmark_has_zero_false_acceptance():
    script = Path(__file__).parents[1] / "scripts" / "run_knowledge_acceptance_eval.py"
    spec = importlib.util.spec_from_file_location("run_knowledge_acceptance_eval", script)
    evaluation = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(evaluation)

    report = evaluation.evaluate_dataset()
    assert report["benchmark_id"] == "claim-test-coverage-v1"
    assert report["case_count"] == 10
    assert report["passed_cases"] == 10
    assert report["domain_count"] == 4
    assert report["confusion"]["false_acceptance_rate"] == 0.0
    assert report["confusion"]["false_rejection_rate"] == 0.0


def test_reference_benchmark_rejects_duplicate_ids_and_schema_drift(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "run_knowledge_acceptance_eval.py"
    spec = importlib.util.spec_from_file_location("run_knowledge_acceptance_eval_bad", script)
    evaluation = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(evaluation)
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id":"duplicate","dataset_version":"v1","domain":"a","claim_ids":["C1"],"test_plan":[{"test":"t","claim_ids":["C1"]}],"expected_decision":"accept"}\n'
        '{"id":"duplicate","dataset_version":"v1","domain":"b","claim_ids":["C1"],"test_plan":[{"test":"t","claim_ids":["C1"]}],"expected_decision":"accept"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        evaluation.load_cases(bad)
