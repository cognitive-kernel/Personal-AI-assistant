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
