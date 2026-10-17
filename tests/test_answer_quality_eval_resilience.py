from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_answer_quality_eval as evaluation


def test_source_audit_failure_does_not_turn_successful_generation_into_failure(
    monkeypatch, tmp_path: Path
) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text("[]", encoding="utf-8")
    cases = [{
        "id": "case-1",
        "language": "en",
        "prompt": "test prompt",
        "expected_concepts": ["expected"],
        "must_not_claim": [],
    }]

    monkeypatch.setattr(
        evaluation,
        "_audit_sources",
        lambda _question: (_ for _ in ()).throw(RuntimeError("private db path")),
    )
    monkeypatch.setattr(evaluation, "get_settings", lambda: {"ollama_model": "local-test"})
    monkeypatch.setattr(evaluation.ollama, "list_models", lambda: [])

    report = evaluation.run_evaluation(
        cases=cases,
        answer_fn=lambda _prompt: "an answer",
        dataset_path=dataset,
        redact_answers=True,
    )

    assert report["run_status"] == "completed"
    assert report["completed_count"] == 1
    assert report["failed_count"] == 0
    assert report["results"][0]["generation_status"] == "completed"
    assert report["results"][0]["source_audit_status"] == "unavailable"
    assert report["results"][0]["source_audit_error_type"] == "RuntimeError"
    assert "answer" not in report["results"][0]
    assert "private db path" not in str(report)
    assert report["source_snapshots"] == [{"case_id": "case-1", "sources": []}]



def test_generation_and_surface_diagnostic_failures_are_reported_separately(
    monkeypatch, tmp_path: Path
) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text("[]", encoding="utf-8")
    cases = [
        {"id": "generation-fails", "language": "en", "prompt": "first", "expected_concepts": [], "must_not_claim": []},
        {"id": "diagnostic-fails", "language": "fa", "prompt": "second", "expected_concepts": [], "must_not_claim": []},
    ]

    def answer_fn(prompt: str) -> str:
        if prompt == "first":
            raise RuntimeError("private C:/Users/example/secret")
        return "پاسخ"

    monkeypatch.setattr(evaluation, "_audit_sources", lambda _question: [])
    monkeypatch.setattr(
        evaluation,
        "evaluate_answer_surface",
        lambda *_args: (_ for _ in ()).throw(ValueError("private diagnostic detail")),
    )
    monkeypatch.setattr(evaluation, "get_settings", lambda: {"ollama_model": "local-test"})
    monkeypatch.setattr(evaluation.ollama, "list_models", lambda: [])

    report = evaluation.run_evaluation(
        cases=cases,
        answer_fn=answer_fn,
        dataset_path=dataset,
        redact_answers=True,
    )

    assert report["run_status"] == "incomplete"
    assert report["completed_count"] == 1
    assert report["failed_count"] == 1
    assert report["surface_diagnostic_completed_count"] == 0
    assert report["surface_diagnostic_failed_count"] == 1
    assert report["results"][0]["generation_status"] == "failed"
    assert report["results"][0]["generation_error_type"] == "RuntimeError"
    assert report["results"][1]["generation_status"] == "completed"
    assert report["results"][1]["surface_diagnostic_status"] == "failed"
    assert report["results"][1]["surface_diagnostic_error_type"] == "ValueError"
    assert "private C:/Users/example/secret" not in str(report)
    assert "private diagnostic detail" not in str(report)
    assert all("answer" not in item for item in report["results"])
