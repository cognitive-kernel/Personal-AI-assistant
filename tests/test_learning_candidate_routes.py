import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import db, learning
from app.main import app


@pytest.fixture
def candidate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    return TestClient(app, client=("127.0.0.1", 12345))


def _candidate(job_id: int, *, content: str = "A lesson held for independent testing.") -> int:
    with db.learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO temporary_knowledge_candidates(
                   job_id, topic, content, source_kind, source_urls_json,
                   content_sha256, quality_report_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                "Candidate review",
                content,
                "user_provided_text",
                json.dumps(["https://docs.python.org/3/"]),
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
                json.dumps({"accepted": True, "score": 90}),
            ),
        )
        return int(cursor.lastrowid)


def test_candidate_list_and_detail_validate_hash_and_decode_metadata(candidate_store):
    job = learning.create_job("Candidate route")
    candidate_id = _candidate(job["id"])

    listing = candidate_store.get("/api/learning/candidates?status=awaiting_test")
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    item = listing.json()["items"][0]
    assert item["id"] == candidate_id
    assert item["integrity_valid"] is True
    assert item["source_urls"] == ["https://docs.python.org/3/"]
    assert item["quality_report"]["accepted"] is True
    assert "content" not in item

    detail = candidate_store.get(f"/api/learning/candidates/{candidate_id}")
    assert detail.status_code == 200
    assert detail.json()["candidate"]["content"] == "A lesson held for independent testing."
    assert detail.json()["candidate"]["integrity_valid"] is True


def test_candidate_test_plan_requires_explicit_confirmation_and_independent_oracle(candidate_store):
    job = learning.create_job("Candidate test plan")
    candidate_id = _candidate(job["id"])
    base = {
        "confirm": True,
        "oracle_reviewed": True,
        "review_note": "The expected result was checked against an independent reference.",
        "tests": [{
            "name": "behavior contract",
            "objective": "Check the stated behavior against a documented contract.",
            "expected_result": "The result must match the documented contract exactly.",
        }],
    }

    no_confirmation = candidate_store.post(
        f"/api/learning/candidates/{candidate_id}/test-plan",
        json={**base, "confirm": False},
    )
    assert no_confirmation.status_code == 422

    no_oracle = candidate_store.post(
        f"/api/learning/candidates/{candidate_id}/test-plan",
        json={**base, "oracle_reviewed": False},
    )
    assert no_oracle.status_code == 422

    one_sided_executable_test = {
        **base,
        "tests": [{
            "name": "behavior contract",
            "objective": "Check the stated behavior against a documented contract.",
            "expected_result": "The result must match the documented contract exactly.",
            "python_test_code": "print('result')",
        }],
    }
    rejected_executable_test = candidate_store.post(
        f"/api/learning/candidates/{candidate_id}/test-plan",
        json=one_sided_executable_test,
    )
    assert rejected_executable_test.status_code == 422

    saved = candidate_store.post(
        f"/api/learning/candidates/{candidate_id}/test-plan",
        json=base,
    )
    assert saved.status_code == 201
    result = saved.json()
    assert result["executed"] is False
    assert result["promoted"] is False
    assert result["test_plan"]["oracle_reviewed"] is True
    assert result["test_plan"]["version"] == 2

    detail = candidate_store.get(f"/api/learning/candidates/{candidate_id}").json()["candidate"]
    assert detail["status"] == "awaiting_test"
    assert detail["test_plan"]["tests"][0]["name"] == "behavior contract"
    with db.learning_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE job_id=?", (job["id"],)
        ).fetchone()[0] == 0


def test_candidate_test_plan_is_immutable_without_a_revision_workflow(candidate_store):
    job = learning.create_job("Immutable candidate test plan")
    candidate_id = _candidate(job["id"])
    plan = {
        "confirm": True,
        "oracle_reviewed": True,
        "review_note": "The expected result was checked against an independent reference.",
        "tests": [{
            "name": "first plan",
            "objective": "Check the stated behavior against a documented contract.",
            "expected_result": "The result must match the documented contract exactly.",
        }],
    }
    assert candidate_store.post(f"/api/learning/candidates/{candidate_id}/test-plan", json=plan).status_code == 201
    changed = {
        **plan,
        "tests": [{
            "name": "replacement plan",
            "objective": "Check a different behavior against the documented contract.",
            "expected_result": "A changed plan must not silently replace the original record.",
        }],
    }
    response = candidate_store.post(f"/api/learning/candidates/{candidate_id}/test-plan", json=changed)
    assert response.status_code == 409
    assert "audited revision" in response.json()["detail"]


def test_candidate_detail_fails_closed_when_content_hash_is_corrupt(candidate_store):
    job = learning.create_job("Corrupt candidate")
    candidate_id = _candidate(job["id"])
    with db.learning_connection() as connection:
        connection.execute(
            "UPDATE temporary_knowledge_candidates SET content='modified after staging' WHERE id=?",
            (candidate_id,),
        )

    response = candidate_store.get(f"/api/learning/candidates/{candidate_id}")
    assert response.status_code == 409
    assert "hash mismatch" in response.json()["detail"]


def test_candidate_api_rejects_unknown_status_and_missing_candidate(candidate_store):
    invalid_filter = candidate_store.get("/api/learning/candidates?status=verified")
    assert invalid_filter.status_code == 422
    missing = candidate_store.get("/api/learning/candidates/999999")
    assert missing.status_code == 404


def test_curated_oracle_catalog_is_versioned_and_fails_closed(review_store):
    from app.learning_oracles import evaluate_candidate_claim, list_oracles

    catalog = list_oracles()
    assert catalog
    assert all(item["version"] == 1 and len(item["source_summary_sha256"]) == 64 for item in catalog)
    assert all(item["catalog_status"] == "provisional_requires_independent_human_review" for item in catalog)
    claim = "Python's json.loads raises JSONDecodeError when the input is not a valid JSON document."
    matched = evaluate_candidate_claim(
        f"Python JSON handling. {claim}",
        oracle_id="python.json-decode-error",
        oracle_version=1,
        claim=claim,
    )
    assert matched["status"] == "matched"
    assert matched["promoted"] is False
    assert matched["catalog_status"] == "provisional_requires_independent_human_review"
    assert matched["source_url"] == "https://docs.python.org/3/library/json.html"

    assert evaluate_candidate_claim(
        "This lesson says nothing about JSON parsing.",
        oracle_id="python.json-decode-error",
        oracle_version=1,
        claim=claim,
    )["reason"] == "claim_not_present_in_candidate"
    assert evaluate_candidate_claim(
        claim,
        oracle_id="python.json-decode-error",
        oracle_version=999,
        claim=claim,
    )["reason"] == "unknown_oracle_version"
    assert evaluate_candidate_claim(
        "Python can parse JSON.",
        oracle_id="python.json-decode-error",
        oracle_version=1,
        claim="Python can parse JSON.",
    )["reason"] == "claim_not_in_curated_reference_set"


def test_test_plan_curated_oracle_must_match_candidate_content(review_store):
    candidate_claim = "Python's json.loads raises JSONDecodeError when the input is not a valid JSON document."
    candidate_id, _job_id = _candidate(content=f"JSON parsing behavior. {candidate_claim}")
    request = {
        "confirm": True,
        "oracle_reviewed": True,
        "review_note": "The curated claim was checked against the versioned reference catalog.",
        "tests": [{
            "name": "json-decode-reference",
            "objective": "Check a narrowly curated Python JSON claim.",
            "expected_result": "The exact curated claim appears in the candidate.",
            "python_test_code": "print('reviewed')",
            "expected_stdout": "reviewed",
            "oracle_id": "python.json-decode-error",
            "oracle_version": 1,
            "claim": candidate_claim,
        }],
    }
    saved = review_store.post(f"/api/learning/candidates/{candidate_id}/test-plan", json=request)
    assert saved.status_code == 201
    assert saved.json()["test_plan"]["version"] == 3

    other_id, _job_id = _candidate(content="Python can parse JSON.")
    bad = review_store.post(
        f"/api/learning/candidates/{other_id}/test-plan",
        json={**request, "tests": [{**request["tests"][0], "claim": "Python can parse JSON."}]},
    )
    assert bad.status_code == 422


def test_oracle_catalog_api_is_read_only(review_store):
    response = review_store.get("/api/learning/candidates/oracles")
    assert response.status_code == 200
    body = response.json()
    assert body["catalog_version"] == 1
    assert body["promoted"] is False
    assert {item["oracle_id"] for item in body["items"]} >= {
        "python.json-decode-error",
        "python.dict-get-default",
        "sqlite.foreign-key-check",
    }
