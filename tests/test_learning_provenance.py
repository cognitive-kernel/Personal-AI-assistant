from __future__ import annotations

import pytest

from app.learning_provenance import (
    LEARNING_PATHS,
    LearningProvenanceError,
    evaluate_aggregate_learning_accuracy,
    validate_learning_source,
    validate_learning_sources,
)


def test_web_source_requires_direct_https_page():
    assert validate_learning_source({
        "source_type": "web",
        "source_url": "https://learn.microsoft.com/en-us/python/",
        "title": "Microsoft Learn",
    })["source_url"].startswith("https://")
    for url in ("", "http://example.com", "https://user:pass@example.com/page", "javascript:alert(1)"):
        with pytest.raises(LearningProvenanceError):
            validate_learning_source({"source_type": "web", "source_url": url})


def test_ai_source_requires_provider_model_and_reference_link():
    source = {
        "source_type": "ai",
        "provider": "OpenAI",
        "model_id": "example-model",
        "reference_url": "https://learn.microsoft.com/en-us/",
    }
    assert validate_learning_source(source)["model_id"] == "example-model"
    for missing in ("provider", "model_id", "reference_url"):
        invalid = dict(source)
        invalid.pop(missing)
        with pytest.raises(LearningProvenanceError):
            validate_learning_source(invalid)


def test_book_source_requires_title_and_author():
    valid = {"source_type": "book", "book_title": "Clean Architecture", "book_author": "Robert C. Martin"}
    assert validate_learning_source(valid)["book_author"] == "Robert C. Martin"
    with pytest.raises(LearningProvenanceError):
        validate_learning_source({"source_type": "book", "book_title": "A book"})


def test_other_sources_are_supported_without_weakening_required_types():
    assert validate_learning_sources([
        {"source_type": "other", "description": "Controlled lab observation"},
        {"source_type": "book", "book_title": "Book", "book_author": "Author"},
    ])[0]["description"] == "Controlled lab observation"
    with pytest.raises(LearningProvenanceError):
        validate_learning_sources([])


def _records(correct: int, incorrect: int = 0):
    paths = sorted(LEARNING_PATHS)
    records = []
    for index, path in enumerate(paths):
        records.append({
            "sample_id": f"sample-{index}",
            "learning_path": path,
            "outcome": "correct",
            "verification_method": "official_source_comparison",
            "verifier_id": "independent-checker",
            "evidence_ref": f"https://docs.example.test/evidence/{index}",
            "evaluated_at": "2026-11-13T10:00:00Z",
        })
    for index in range(correct - len(paths)):
        records.append({
            "sample_id": f"correct-{index}",
            "learning_path": paths[index % len(paths)],
            "outcome": "correct",
            "verification_method": "deterministic_oracle",
            "verifier_id": "oracle-v1",
            "evidence_ref": f"oracle://case/{index}",
            "evaluated_at": "2026-11-13T10:00:00Z",
        })
    for index in range(incorrect):
        records.append({
            "sample_id": f"incorrect-{index}",
            "learning_path": paths[index % len(paths)],
            "outcome": "incorrect",
            "verification_method": "sandbox_test",
            "verifier_id": "sandbox-v1",
            "evidence_ref": f"artifact://test/{index}",
            "evaluated_at": "2026-11-13T10:00:00Z",
        })
    return records


def test_aggregate_gate_requires_strictly_more_than_95_percent_and_all_paths():
    report = evaluate_aggregate_learning_accuracy(_records(95, 5))
    assert report["accuracy"] == pytest.approx(0.95)
    assert report["autonomous_acceptance_eligible"] is False
    report = evaluate_aggregate_learning_accuracy(_records(96, 4))
    assert report["accuracy"] == pytest.approx(0.96)
    assert report["autonomous_acceptance_eligible"] is True


def test_aggregate_gate_requires_all_four_learning_paths():
    records = _records(96, 4)
    records = [item for item in records if item["learning_path"] != "experience_system"]
    report = evaluate_aggregate_learning_accuracy(records)
    assert report["autonomous_acceptance_eligible"] is False


def test_evaluation_rejects_duplicate_or_self_verified_samples():
    records = _records(4)
    records.append(dict(records[0]))
    with pytest.raises(LearningProvenanceError, match="duplicated"):
        evaluate_aggregate_learning_accuracy(records)

    records = _records(4)
    records[0]["verification_method"] = "independent_model"
    records[0]["generator_id"] = records[0]["verifier_id"]
    with pytest.raises(LearningProvenanceError, match="generating model"):
        evaluate_aggregate_learning_accuracy(records)


def test_evaluation_rejects_missing_evidence_and_timezone():
    records = _records(4)
    records[0]["evidence_ref"] = ""
    with pytest.raises(LearningProvenanceError, match="evidence_ref"):
        evaluate_aggregate_learning_accuracy(records)
    records = _records(4)
    records[0]["evaluated_at"] = "2026-11-13T10:00:00"
    with pytest.raises(LearningProvenanceError, match="timezone"):
        evaluate_aggregate_learning_accuracy(records)
