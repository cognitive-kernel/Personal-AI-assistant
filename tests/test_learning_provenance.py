from __future__ import annotations

import pytest

from app.learning_policy_errors import LearningPolicyError
from app.learning_accuracy_gate import (
    LEARNING_PATHS,
    evaluate_aggregate_learning_accuracy,
    record_accuracy_evaluation,
    get_aggregate_learning_accuracy,
)
from app.learning_source_provenance import (
    validate_learning_source,
    validate_learning_sources,
    list_candidate_sources,
    record_candidate_source,
)


def test_web_source_requires_direct_https_page():
    assert validate_learning_source({
        "source_type": "web",
        "source_url": "https://learn.microsoft.com/en-us/python/",
        "title": "Microsoft Learn",
    })["source_url"].startswith("https://")
    for url in ("", "http://example.com", "https://user:pass@example.com/page", "javascript:alert(1)"):
        with pytest.raises(LearningPolicyError):
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
        with pytest.raises(LearningPolicyError):
            validate_learning_source(invalid)


def test_book_source_requires_title_and_author():
    valid = {"source_type": "book", "book_title": "Clean Architecture", "book_author": "Robert C. Martin"}
    assert validate_learning_source(valid)["book_author"] == "Robert C. Martin"
    with pytest.raises(LearningPolicyError):
        validate_learning_source({"source_type": "book", "book_title": "A book"})


def test_other_sources_are_supported_without_weakening_required_types():
    assert validate_learning_sources([
        {"source_type": "other", "description": "Controlled lab observation"},
        {"source_type": "book", "book_title": "Book", "book_author": "Author"},
    ])[0]["description"] == "Controlled lab observation"
    with pytest.raises(LearningPolicyError):
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
    with pytest.raises(LearningPolicyError, match="duplicated"):
        evaluate_aggregate_learning_accuracy(records)

    records = _records(4)
    records[0]["verification_method"] = "independent_model"
    records[0]["generator_id"] = records[0]["verifier_id"]
    with pytest.raises(LearningPolicyError, match="generating model"):
        evaluate_aggregate_learning_accuracy(records)


def test_evaluation_rejects_missing_evidence_and_timezone():
    records = _records(4)
    records[0]["evidence_ref"] = ""
    with pytest.raises(LearningPolicyError, match="evidence_ref"):
        evaluate_aggregate_learning_accuracy(records)
    records = _records(4)
    records[0]["evaluated_at"] = "2026-11-13T10:00:00"
    with pytest.raises(LearningPolicyError, match="timezone"):
        evaluate_aggregate_learning_accuracy(records)


def test_candidate_source_records_are_persisted_idempotently_and_do_not_promote(tmp_path, monkeypatch):
    import hashlib
    import json

    from app import db, learning
    from app.learning_source_provenance import list_candidate_sources, record_candidate_source

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Source provenance persistence", source_text="User supplied source text.")
    content = "Candidate content must remain unverified."
    with db.learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                job["id"], "Source provenance persistence", content, "user_provided_text",
                "[]", hashlib.sha256(content.encode("utf-8")).hexdigest(),
                json.dumps({"score": 90}),
            ),
        )
        candidate_id = int(cursor.lastrowid)

    source = {
        "source_type": "ai",
        "provider": "OpenAI",
        "model_id": "test-model",
        "reference_url": "https://learn.microsoft.com/en-us/",
        "reference_section": "Overview",
    }
    first = record_candidate_source(candidate_id, source)
    second = record_candidate_source(candidate_id, source)
    assert first["id"] == second["id"]
    assert first["verification_status"] == "unverified"
    assert len(list_candidate_sources(candidate_id)) == 1
    with db.learning_connection() as connection:
        status = connection.execute(
            "SELECT status, promoted_knowledge_id FROM temporary_knowledge_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    assert status["status"] == "awaiting_test"
    assert status["promoted_knowledge_id"] is None


def test_persisted_accuracy_gate_is_trusted_idempotent_and_integrity_checked(tmp_path, monkeypatch):
    import sqlite3

    from app import db
    from app.learning_accuracy_gate import (
        get_aggregate_learning_accuracy,
        record_accuracy_evaluation,
    )

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    with pytest.raises(LearningPolicyError, match="trusted verifier"):
        record_accuracy_evaluation(_records(4)[0])

    records = _records(96, 4)
    first = record_accuracy_evaluation(records[0], trusted_adapter=True)
    repeated = record_accuracy_evaluation(records[0], trusted_adapter=True)
    assert first["id"] == repeated["id"]

    for record in records[1:]:
        record_accuracy_evaluation(record, trusted_adapter=True)

    report = get_aggregate_learning_accuracy()
    assert report["sample_count"] == 100
    assert report["correct_count"] == 96
    assert report["accuracy"] == pytest.approx(0.96)
    assert report["autonomous_acceptance_eligible"] is True

    with db.learning_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE learning_accuracy_evaluations SET outcome='incorrect' WHERE sample_id=?",
                (records[0]["sample_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM learning_accuracy_evaluations WHERE sample_id=?",
                (records[0]["sample_id"],),
            )


def test_accuracy_record_id_cannot_be_reused_with_different_evidence(tmp_path, monkeypatch):
    from app import db
    from app.learning_accuracy_gate import record_accuracy_evaluation

    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    record = _records(4)[0]
    record_accuracy_evaluation(record, trusted_adapter=True)
    changed = dict(record, outcome="incorrect")
    with pytest.raises(LearningPolicyError, match="cannot be reused"):
        record_accuracy_evaluation(changed, trusted_adapter=True)
