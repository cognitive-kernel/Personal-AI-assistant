import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, ollama
from app.answer_quality_evaluation import evaluate_answer_surface, summarize_surface_results
from app.db import learning_connection
import scripts.run_answer_quality_eval as answer_quality_runner
from scripts.run_answer_quality_eval import run_evaluation


def _case(language="en"):
    if language == "fa":
        return {
            "id": "fa-test",
            "language": "fa",
            "prompt": "پرسش آزمایشی",
            "expected_concepts": ["منبع معتبر", "عدم قطعیت"],
            "evidence_policy": "Keep uncertainty explicit.",
            "must_not_claim": ["تأیید مستقل بدون شواهد"],
        }
    return {
        "id": "en-test",
        "language": "en",
        "prompt": "test prompt",
        "expected_concepts": ["source provenance", "state uncertainty"],
        "evidence_policy": "Keep uncertainty explicit.",
        "must_not_claim": ["independently verified without evidence"],
    }


def test_surface_evaluation_normalizes_persian_letter_variants():
    case = _case("fa")
    result = evaluate_answer_surface(
        case,
        "بررسی منبع معتبر و عدم قطعیت ضروری است.",
        [{"url": "https://example.com/source", "title": "Source"}],
    )
    assert result["surface_matched_concepts"] == ["منبع معتبر", "عدم قطعیت"]
    assert result["surface_concept_coverage"] == 1.0
    assert result["source_count"] == 1
    assert "phrase presence does not prove correctness" in result["interpretation"]


def test_surface_evaluation_reports_prohibited_phrases_and_language():
    result = evaluate_answer_surface(
        _case(),
        "This was independently verified without evidence. We should state uncertainty.",
        [],
    )
    assert result["prohibited_phrase_hits"] == ["independently verified without evidence"]
    assert result["expected_language_script_present"] is True
    assert result["source_count"] == 0


def test_summary_is_by_language_and_not_called_a_quality_score():
    results = [
        evaluate_answer_surface(_case("fa"), "منبع معتبر؛ عدم قطعیت.", []),
        evaluate_answer_surface(_case("en"), "source provenance; state uncertainty.", []),
    ]
    summary = summarize_surface_results(results)
    assert summary["case_count"] == 2
    assert set(summary["languages"]) == {"fa", "en"}
    assert "Not a model-quality score" in summary["interpretation"]


def test_runner_reports_failures_and_metadata_without_claiming_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    cases = [_case("en"), _case("fa")]
    model_digest = "sha256:" + "b" * 64
    monkeypatch.setattr(ollama, "list_models", lambda: [{
        "name": "default-model",
        "size": 123,
        "modified_at": "2026-10-16T00:00:00Z",
        "digest": model_digest,
    }])
    db.update_settings({"ollama_model": "default-model"})

    def answer(prompt):
        if prompt == "پرسش آزمایشی":
            raise RuntimeError("model unavailable")
        return "source provenance and state uncertainty"

    report = run_evaluation(
        cases=cases,
        answer_fn=answer,
        dataset_path=dataset,
        redact_answers=True,
    )
    assert report["run_status"] == "incomplete"
    assert report["completed_count"] == 1
    assert report["failed_count"] == 1
    assert report["generation_completed_count"] == 1
    assert report["generation_failed_count"] == 1
    assert report["quality_assessment_status"] == "not_scored"
    assert "answer" not in report["results"][0]
    assert report["results"][1]["generation_status"] == "failed"
    assert report["dataset_sha256"]
    assert report["prompt_code_sha256"]["chat_research.py"]
    assert report["model"]["manifest_digest"] == model_digest
    assert report["surface_diagnostics"]["case_count"] == 1
    assert "not establish factual correctness" in report["interpretation"]

def test_cli_summary_distinguishes_generation_from_quality_status(tmp_path, monkeypatch, capsys):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "report.json"
    monkeypatch.setattr(answer_quality_runner, "load_cases", lambda _path: [_case()])
    monkeypatch.setattr(db, "init_databases", lambda: None)
    monkeypatch.setattr(
        answer_quality_runner,
        "run_evaluation",
        lambda **_kwargs: {
            "run_status": "completed",
            "generation_completed_count": 1,
            "generation_failed_count": 0,
            "quality_assessment_status": "not_scored",
            "surface_diagnostics": {"case_count": 1},
            "interpretation": "Surface diagnostics do not establish factual correctness.",
            "results": [],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_answer_quality_eval.py",
            "--dataset",
            str(dataset),
            "--output",
            str(output),
            "--redact-answers",
        ],
    )

    assert answer_quality_runner.main() == 0
    summary = json.loads(capsys.readouterr().out)
    saved_report = json.loads(output.read_text(encoding="utf-8"))

    assert summary["completed_count"] == 1
    assert summary["generation_failed_count"] == 0
    assert summary["quality_assessment_status"] == "not_scored"
    assert saved_report["quality_assessment_status"] == "not_scored"


def test_surface_evaluation_flags_answer_urls_without_structured_source_attribution():
    case = _case("en")
    answer = (
        "The guide is available at https://docs.example.org/guide. "
        "Another claimed reference is https://example.invalid/not-retrieved)."
    )
    result = evaluate_answer_surface(
        case,
        answer,
        [{"url": "https://docs.example.org/guide", "title": "Official Guide"}],
    )

    assert result["answer_urls"] == [
        "https://docs.example.org/guide",
        "https://example.invalid/not-retrieved",
    ]
    assert result["source_urls"] == ["https://docs.example.org/guide"]
    assert result["unattributed_answer_urls"] == ["https://example.invalid/not-retrieved"]
    assert result["unattributed_answer_url_count"] == 1

    summary = summarize_surface_results([result])
    assert summary["languages"]["en"]["cases_with_unattributed_answer_urls"] == 1
    assert summary["languages"]["en"]["unattributed_answer_url_count"] == 1


def test_surface_evaluation_does_not_flag_exact_structured_source_urls():
    result = evaluate_answer_surface(
        _case("en"),
        "See https://docs.example.org/guide for the details.",
        [{"url": "https://docs.example.org/guide", "title": "Official Guide"}],
    )
    assert result["unattributed_answer_urls"] == []
    assert result["unattributed_answer_url_count"] == 0
