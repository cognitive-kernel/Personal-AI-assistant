import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_learning_assessment_eval.py"
SPEC = importlib.util.spec_from_file_location("run_learning_assessment_eval", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(evaluation)


def test_versioned_learning_assessment_benchmark_matches_all_expected_cases():
    report = evaluation.evaluate_dataset()

    assert report["benchmark_id"] == "learning-rubric-lexical-v1"
    assert report["case_count"] == 8
    assert report["passed_cases"] == 8
    assert report["regression_accuracy_percent"] == 100.0
    assert report["status"] == "passed"
    assert report["interpretation"].find("does not validate") >= 0


def test_learning_assessment_benchmark_fails_closed_for_wrong_schema(tmp_path):
    dataset = tmp_path / "invalid.json"
    dataset.write_text('{"schema_version": 999, "cases": []}', encoding="utf-8")

    try:
        evaluation.evaluate_dataset(dataset)
    except ValueError as exc:
        assert "schema" in str(exc).lower()
    else:
        raise AssertionError("Unsupported benchmark schema should fail closed.")
