import json

import pytest

from scripts.evaluate_learning_acceptance import (
    EvaluationDataError,
    evaluate,
    load_dataset,
    main,
)


def _case(case_id, expected, observed):
    return {
        "id": case_id,
        "domain": "python",
        "language": "en",
        "expected_decision": expected,
        "observed_decision": observed,
        "source_refs": ["https://docs.python.org/3/"],
        "adjudication": {
            "reviewer": "independent-reviewer",
            "adjudicated_at": "2026-10-21T12:00:00Z",
            "rationale": "Reviewed against the cited primary source and acceptance policy.",
            "independent_of_model": True,
        },
    }


def _dataset(cases):
    return {"schema_version": 1, "dataset_version": "test-v1", "cases": cases}


def test_evaluate_reports_false_accepts_and_false_rejects():
    report = evaluate([
        _case("ta", "accept", "accept"),
        _case("fa", "reject", "accept"),
        _case("tr", "reject", "reject"),
        _case("fr", "accept", "reject"),
    ])

    assert report["case_count"] == 4
    assert report["confusion_matrix"] == {
        "true_accept": 1,
        "false_accept": 1,
        "true_reject": 1,
        "false_reject": 1,
    }
    assert report["metrics"]["precision"] == pytest.approx(0.5)
    assert report["metrics"]["recall"] == pytest.approx(0.5)
    assert report["metrics"]["f1"] == pytest.approx(0.5)
    assert report["metrics"]["false_acceptance_rate"] == pytest.approx(0.5)
    assert report["metrics"]["false_rejection_rate"] == pytest.approx(0.5)
    assert report["metrics"]["accuracy"] == pytest.approx(0.5)


def test_metrics_use_null_when_a_denominator_is_absent():
    report = evaluate([_case("tr", "reject", "reject")])

    assert report["metrics"]["precision"] is None
    assert report["metrics"]["recall"] is None
    assert report["metrics"]["false_acceptance_rate"] == 0
    assert report["metrics"]["false_rejection_rate"] is None


def test_dataset_requires_independent_human_adjudication(tmp_path):
    case = _case("one", "accept", "accept")
    case["adjudication"]["independent_of_model"] = False
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(_dataset([case])), encoding="utf-8")

    with pytest.raises(EvaluationDataError, match="independent_of_model"):
        load_dataset(path)


def test_dataset_rejects_duplicate_case_ids(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(_dataset([_case("same", "accept", "accept"), _case("same", "reject", "reject")])),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationDataError, match="duplicate case id"):
        load_dataset(path)


def test_cli_enforces_explicit_false_acceptance_threshold(tmp_path, capsys):
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(_dataset([
            _case("true-reject", "reject", "reject"),
            _case("false-accept", "reject", "accept"),
        ])),
        encoding="utf-8",
    )

    exit_code = main([str(path), "--max-false-acceptance-rate", "0"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["dataset_version"] == "test-v1"
    assert output["metrics"]["false_acceptance_rate"] == pytest.approx(0.5)
