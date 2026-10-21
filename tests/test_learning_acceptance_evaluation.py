import json

import pytest

from scripts.evaluate_learning_acceptance import (
    EvaluationDataError,
    evaluate,
    load_dataset,
    load_observations,
    main,
)


def _case(case_id, expected):
    return {
        "id": case_id,
        "domain": "python",
        "language": "en",
        "expected_decision": expected,
        "source_refs": ["https://docs.python.org/3/"],
        "adjudication": {
            "reviewer": "independent-reviewer",
            "adjudicated_at": "2026-10-21T12:00:00Z",
            "rationale": "Reviewed against the cited primary source and acceptance policy.",
            "independent_of_model": True,
        },
    }


def _dataset(cases):
    return {
        "schema_version": 1,
        "dataset_name": "test-learning-acceptance",
        "dataset_version": "test-v1",
        "cases": cases,
    }


def _observations(pairs):
    return {
        "schema_version": 1,
        "run_metadata": {
            "model_id": "test-model",
            "model_digest": "sha256:test-digest",
            "prompt_version": "learning-review-v1",
            "application_commit": "abc123",
            "evaluated_at": "2026-10-21T12:30:00Z",
        },
        "observations": [
            {"id": case_id, "observed_decision": decision}
            for case_id, decision in pairs
        ],
    }


def test_evaluate_reports_false_accepts_and_false_rejects():
    cases = [
        _case("ta", "accept"),
        _case("fa", "reject"),
        _case("tr", "reject"),
        _case("fr", "accept"),
    ]
    observations = _observations([
        ("ta", "accept"),
        ("fa", "accept"),
        ("tr", "reject"),
        ("fr", "reject"),
    ])["observations"]
    report = evaluate(cases, observations)

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
    report = evaluate([_case("tr", "reject")], [{"id": "tr", "observed_decision": "reject"}])

    assert report["metrics"]["precision"] is None
    assert report["metrics"]["recall"] is None
    assert report["metrics"]["f1"] is None
    assert report["metrics"]["false_acceptance_rate"] == 0
    assert report["metrics"]["false_rejection_rate"] is None


def test_dataset_requires_independent_human_adjudication(tmp_path):
    case = _case("one", "accept")
    case["adjudication"]["independent_of_model"] = False
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(_dataset([case])), encoding="utf-8")

    with pytest.raises(EvaluationDataError, match="independent_of_model"):
        load_dataset(path)


def test_dataset_rejects_duplicate_case_ids(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(_dataset([_case("same", "accept"), _case("same", "reject")])),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationDataError, match="duplicate case id"):
        load_dataset(path)


def test_evaluation_rejects_missing_or_unknown_observation_ids():
    with pytest.raises(EvaluationDataError, match="missing observations"):
        evaluate([_case("expected", "accept")], [])

    with pytest.raises(EvaluationDataError, match="unknown observation IDs"):
        evaluate([_case("expected", "accept")], [
            {"id": "unexpected", "observed_decision": "accept"}
        ])


def test_observations_require_reproducibility_metadata(tmp_path):
    data = _observations([("one", "accept")])
    del data["run_metadata"]["model_digest"]
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationDataError, match="run_metadata.model_digest"):
        load_observations(path)


def test_cli_enforces_explicit_false_acceptance_threshold(tmp_path, capsys):
    dataset_path = tmp_path / "dataset.json"
    observations_path = tmp_path / "observations.json"
    dataset_path.write_text(
        json.dumps(_dataset([_case("true-reject", "reject"), _case("false-accept", "reject")])),
        encoding="utf-8",
    )
    observations_path.write_text(
        json.dumps(_observations([("true-reject", "reject"), ("false-accept", "accept")])),
        encoding="utf-8",
    )

    exit_code = main([
        str(dataset_path), str(observations_path),
        "--max-false-acceptance-rate", "0",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["dataset_version"] == "test-v1"
    assert output["run_metadata"]["application_commit"] == "abc123"
    assert output["metrics"]["false_acceptance_rate"] == pytest.approx(0.5)
