import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_learning_transfer_eval.py"
SPEC = importlib.util.spec_from_file_location("run_learning_transfer_eval", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(evaluation)


def test_versioned_bilingual_transfer_gate_benchmark():
    report = evaluation.evaluate_dataset()

    assert report["benchmark_id"] == "learning-transfer-prompt-v1"
    assert report["case_count"] == 16
    assert report["passed_cases"] == 16
    assert report["accuracy_percent"] == 100.0
    assert report["status"] == "passed"
    assert "does not prove semantic transfer" in report["interpretation"]
