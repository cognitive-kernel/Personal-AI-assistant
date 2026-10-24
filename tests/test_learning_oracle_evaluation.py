import pytest

from app.learning_oracle_evaluation import (
    OracleEvaluationError,
    evaluate_oracle_cases,
    validate_oracle_dataset,
)


def _case(case_id, expected, *, content, claim):
    return {
        "id": case_id,
        "oracle_id": "python.dict-get-default",
        "oracle_version": 1,
        "content": content,
        "claim": claim,
        "expected_decision": expected,
        "source_url": "https://docs.python.org/3/library/stdtypes.html#dict.get",
        "source_sha256": "a" * 64,
        "adjudication": {
            "reviewer": "independent-human-reviewer",
            "adjudicated_at": "2026-10-24T06:00:00Z",
            "rationale": "Compared the claim and candidate context with the cited primary source.",
            "independent_of_model": True,
        },
    }


def test_oracle_evaluation_reports_false_acceptance_and_false_rejection():
    canonical = "Python dict.get returns the default value when the key is absent."
    cases = [
        _case("true-match", "match", content=canonical, claim=canonical),
        _case("false-accept", "no_match", content=canonical + " This statement is false in this context.", claim=canonical),
        _case("true-nonmatch", "no_match", content="A lesson about lists.", claim=canonical),
        _case("false-reject", "match", content="The dict.get method yields the fallback if a key is missing.", claim=canonical),
    ]

    report = evaluate_oracle_cases(cases)

    assert report["case_count"] == 4
    assert report["confusion_matrix"] == {
        "true_match": 1,
        "false_match": 1,
        "true_nonmatch": 1,
        "false_nonmatch": 1,
    }
    assert report["metrics"]["accuracy"] == pytest.approx(0.5)
    assert report["metrics"]["precision"] == pytest.approx(0.5)
    assert report["metrics"]["recall"] == pytest.approx(0.5)
    assert report["metrics"]["false_acceptance_rate"] == pytest.approx(0.5)
    assert report["metrics"]["false_rejection_rate"] == pytest.approx(0.5)
    per_oracle = report["per_oracle"]["python.dict-get-default@1"]
    assert per_oracle["case_count"] == 4
    assert per_oracle["metrics"]["false_acceptance_rate"] == pytest.approx(0.5)
    assert {case["oracle_id"] for case in report["per_case"]} == {"python.dict-get-default"}
    assert "not a domain-wide scientific factuality score" in report["interpretation"]


def test_oracle_evaluation_uses_null_for_missing_metric_denominators():
    canonical = "Python dict.get returns the default value when the key is absent."
    report = evaluate_oracle_cases([
        _case("only-negative", "no_match", content="A lesson about lists.", claim=canonical),
    ])

    assert report["metrics"]["precision"] is None
    assert report["metrics"]["recall"] is None
    assert report["metrics"]["false_acceptance_rate"] == 0
    assert report["metrics"]["false_rejection_rate"] is None


def test_oracle_evaluation_requires_independent_adjudication_and_source_hash():
    canonical = "Python dict.get returns the default value when the key is absent."
    case = _case("unreviewed", "match", content=canonical, claim=canonical)
    case["adjudication"]["independent_of_model"] = False
    with pytest.raises(OracleEvaluationError, match="independent_of_model"):
        validate_oracle_dataset({
            "schema_version": 1,
            "dataset_name": "test",
            "dataset_version": "v1",
            "cases": [case],
        })

    case["adjudication"]["independent_of_model"] = True
    case["source_sha256"] = "not-a-hash"
    with pytest.raises(OracleEvaluationError, match="SHA-256"):
        validate_oracle_dataset({
            "schema_version": 1,
            "dataset_name": "test",
            "dataset_version": "v1",
            "cases": [case],
        })


def test_oracle_evaluation_rejects_boolean_version_and_duplicate_case_ids():
    canonical = "Python dict.get returns the default value when the key is absent."
    case = _case("duplicate", "match", content=canonical, claim=canonical)
    invalid_version = dict(case, oracle_version=True)
    with pytest.raises(OracleEvaluationError, match="positive integer"):
        validate_oracle_dataset({
            "schema_version": 1, "dataset_name": "test", "dataset_version": "v1",
            "cases": [invalid_version],
        })

    with pytest.raises(OracleEvaluationError, match="duplicate case id"):
        validate_oracle_dataset({
            "schema_version": 1, "dataset_name": "test", "dataset_version": "v1",
            "cases": [case, dict(case)],
        })


def test_oracle_evaluation_rejects_adjudication_timestamp_without_timezone():
    canonical = "Python dict.get returns the default value when the key is absent."
    case = _case("naive-time", "match", content=canonical, claim=canonical)
    case["adjudication"]["adjudicated_at"] = "2026-10-24T06:00:00"
    with pytest.raises(OracleEvaluationError, match="explicit timezone"):
        validate_oracle_dataset({
            "schema_version": 1,
            "dataset_name": "test",
            "dataset_version": "v1",
            "cases": [case],
        })
