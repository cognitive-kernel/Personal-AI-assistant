import hashlib
import json

from app import db, learning
from app.claim_test_coverage import extract_candidate_claims


def _candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    job = learning.create_job("Claim coverage promotion", source_text="Source text retained for verification.")
    lesson = (
        "The first claim is meaningful and should be checked by its own test. "
        "The second claim is meaningful and also requires a separate explicit test."
    )
    digest = hashlib.sha256(lesson.encode("utf-8")).hexdigest()
    with db.learning_connection() as conn:
        candidate_id = conn.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json, status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_test')""",
            (job["id"], job["topic"], lesson, "user_provided_text",
             json.dumps([]), digest, json.dumps({"accepted": True})),
        ).lastrowid
    return int(candidate_id)


def _plan_and_results(candidate_id, mapped_claim_ids):
    expected = {"result": "ok"}
    oracle_sha256 = hashlib.sha256(
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    plan = []
    results = []
    for index, claim_id in enumerate(mapped_claim_ids, start=1):
        test_name = f"test-claim-{index}"
        plan.append({
            "test": test_name,
            "oracle_kind": "regression_fixture",
            "oracle_ref": "tests/fixtures/learning/reference-v1.json",
            "expected": expected,
            "oracle_sha256": oracle_sha256,
            "claim_ids": [claim_id],
        })
        results.append({
            "test": test_name,
            "passed": True,
            "runner": "reference_dataset",
            "expected": expected,
            "actual": expected,
            "oracle_sha256": oracle_sha256,
        })
    return plan, results


def test_candidate_promotes_only_after_all_claims_have_explicit_passing_tests(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    from app.claim_test_coverage import extract_candidate_claims
    with db.learning_connection() as conn:
        content = conn.execute(
            "SELECT content FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["content"]
    claim_ids = [claim["claim_id"] for claim in extract_candidate_claims(content, candidate_id)]
    assert len(claim_ids) == 2
    plan, results = _plan_and_results(candidate_id, claim_ids)

    recorded = learning.record_candidate_test_results(candidate_id, plan, results)

    assert recorded["promotion_eligible"] is True
    assert recorded["claim_coverage"]["complete"] is True
    assert recorded["claim_coverage"]["coverage_ratio"] == 1.0
    assert recorded["status"] == "passed"
    assert recorded["promoted_knowledge_id"] is not None
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE status='verified'"
        ).fetchone()[0] == 1


def test_all_tests_passing_does_not_promote_when_any_claim_is_unmapped(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        content = conn.execute(
            "SELECT content FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["content"]
    claim_ids = [claim["claim_id"] for claim in extract_candidate_claims(content, candidate_id)]
    assert len(claim_ids) == 2
    plan, results = _plan_and_results(candidate_id, claim_ids[:1])

    recorded = learning.record_candidate_test_results(candidate_id, plan, results)

    assert recorded["tests_passed"] is True
    assert recorded["promotion_eligible"] is False
    assert recorded["claim_coverage"]["complete"] is False
    assert recorded["claim_coverage"]["missing_claim_ids"] == [claim_ids[1]]
    assert recorded["status"] == "awaiting_test"
    assert recorded["promoted_knowledge_id"] is None
    assert "Claim test coverage incomplete" in recorded["failure_reason"]
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE status='verified'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM learning_evidence WHERE evidence_type='candidate_test_coverage_block'"
        ).fetchone()[0] == 1
        run = conn.execute(
            """SELECT suite_name, suite_version, source_sha256, run_status, details
               FROM knowledge_test_runs WHERE candidate_id=? ORDER BY id DESC LIMIT 1""",
            (candidate_id,),
        ).fetchone()
        assert run["suite_name"] == "claim-mapped-candidate-suite"
        assert run["suite_version"] == "claim-test-coverage-v1"
        assert run["source_sha256"] == recorded["actual_content_sha256"]
        assert run["run_status"] == "passed"
        run_details = json.loads(run["details"])
        assert run_details["promotion_eligible"] is False
        assert run_details["runner_labels_source"].startswith("reported by the caller")


def test_passing_tests_with_unknown_claim_mapping_cannot_promote(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        content = conn.execute(
            "SELECT content FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["content"]
    claim_ids = [claim["claim_id"] for claim in extract_candidate_claims(content, candidate_id)]
    plan, results = _plan_and_results(candidate_id, [claim_ids[0], "unknown:claim"])
    # Each result remains correctly aligned with the two planned tests.
    recorded = learning.record_candidate_test_results(candidate_id, plan, results)

    assert recorded["tests_passed"] is True
    assert recorded["promotion_eligible"] is False
    assert recorded["claim_coverage"]["unknown_claim_ids"] == ["unknown:claim"]
    assert recorded["status"] == "awaiting_test"
    assert recorded["promoted_knowledge_id"] is None


def test_modified_candidate_content_hash_blocks_otherwise_passing_coverage(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        content = conn.execute(
            "SELECT content FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["content"]
    claim_ids = [claim["claim_id"] for claim in extract_candidate_claims(content, candidate_id)]
    plan, results = _plan_and_results(candidate_id, claim_ids)
    with db.learning_connection() as conn:
        conn.execute(
            "UPDATE temporary_knowledge_candidates SET content=? WHERE id=?",
            (content + " A new untested claim was inserted after the stored digest.", candidate_id),
        )

    recorded = learning.record_candidate_test_results(candidate_id, plan, results)

    assert recorded["tests_passed"] is True
    assert recorded["content_hash_matches"] is False
    assert recorded["promotion_eligible"] is False
    assert "candidate_content_sha256_mismatch" in recorded["claim_coverage"]["issues"]
    assert recorded["status"] == "awaiting_test"
    assert recorded["promoted_knowledge_id"] is None
    with db.learning_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE status='verified'"
        ).fetchone()[0] == 0



def test_source_example_oracle_is_evidence_but_never_claim_coverage(tmp_path, monkeypatch):
    candidate_id = _candidate(tmp_path, monkeypatch)
    with db.learning_connection() as conn:
        content = conn.execute(
            "SELECT content FROM temporary_knowledge_candidates WHERE id=?", (candidate_id,)
        ).fetchone()["content"]
        conn.execute(
            "UPDATE temporary_knowledge_candidates SET source_urls_json=? WHERE id=?",
            ('["https://www.python.org/"]', candidate_id),
        )
    claim_ids = [claim["claim_id"] for claim in extract_candidate_claims(content, candidate_id)]
    expected = "source-example-output"
    oracle_sha256 = hashlib.sha256(
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    plan = [{
        "test": "source-example-only",
        "oracle_kind": "source_example",
        "oracle_ref": "https://www.python.org/",
        "expected": expected,
        "oracle_sha256": oracle_sha256,
        "claim_ids": claim_ids,
    }]
    results = [{
        "test": "source-example-only", "passed": True, "runner": "sandbox",
        "expected": expected, "actual": expected, "oracle_sha256": oracle_sha256,
    }]

    recorded = learning.record_candidate_test_results(candidate_id, plan, results)

    assert recorded["tests_passed"] is True
    assert recorded["claim_coverage"]["complete"] is False
    assert recorded["claim_coverage"]["promotion_test_count"] == 0
    assert recorded["claim_coverage"]["missing_claim_ids"] == claim_ids
    assert recorded["promotion_eligible"] is False
    assert recorded["status"] == "awaiting_test"
    assert recorded["promoted_knowledge_id"] is None
