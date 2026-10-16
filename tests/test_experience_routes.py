from fastapi.testclient import TestClient

from app import cloud_resources, db, experience, experience_source_checks, web_sources
from app.main import app


def _isolate_app(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(
        db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db")
    )
    monkeypatch.setattr(
        cloud_resources,
        "ensure_daily_discovery_scheduled",
        lambda: {"status": "disabled_for_test"},
    )


def test_experience_api_create_update_list_and_version_history(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post(
            "/api/experiences",
            json={
                "topic": "Python parser",
                "problem": "Parser failed on an empty payload.",
                "hypothesis": "The empty-input branch is missing.",
            },
        )
        assert created.status_code == 201
        item = created.json()["experience"]
        assert item["status"] == "identified"
        experience_id = item["id"]

        updated = client.patch(
            f"/api/experiences/{experience_id}",
            json={
                "status": "needs_review",
                "source_evidence": [
                    {
                        "url": "https://docs.python.org/3/library/",
                        "claim": "Official Python documentation.",
                    }
                ],
                "test_plan": [{"test": "empty payload regression case"}],
                "takeaway": "Reproduce the empty payload before changing the parser.",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["experience"]["version"] == 2
        assert updated.json()["experience"]["status"] == "needs_review"

        listed = client.get("/api/experiences", params={"topic": "Python", "status": "needs_review"})
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["items"][0]["id"] == experience_id

        detail = client.get(f"/api/experiences/{experience_id}")
        assert detail.status_code == 200
        assert detail.json()["experience"]["takeaway"] == (
            "Reproduce the empty payload before changing the parser."
        )

        versions = client.get(f"/api/experiences/{experience_id}/versions")
        assert versions.status_code == 200
        assert {row["version"] for row in versions.json()["versions"]} == {1, 2}


def test_experience_api_cannot_verify_or_inject_test_results(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post(
            "/api/experiences",
            json={"topic": "SQL", "problem": "A query returned an unexpected result."},
        )
        experience_id = created.json()["experience"]["id"]

        promote = client.patch(
            f"/api/experiences/{experience_id}",
            json={
                "status": "verified",
                "source_evidence": [
                    {"url": "https://learn.microsoft.com/sql/", "claim": "Official docs"}
                ],
                "test_plan": [{"test": "regression"}],
            },
        )
        assert promote.status_code == 422

        inject_result = client.patch(
            f"/api/experiences/{experience_id}",
            json={"test_results": [{"test": "forged", "passed": True}]},
        )
        assert inject_result.status_code == 422

        missing = client.get("/api/experiences/999999")
        assert missing.status_code == 404


def test_experience_api_requires_loopback_client(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/experiences")
    assert response.status_code == 403



def test_experience_source_revalidation_requires_confirmation_and_preserves_record(
    tmp_path, monkeypatch
):
    _isolate_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        experience_source_checks.web_sources, "_validate_public_url", lambda url: None
    )
    monkeypatch.setattr(
        experience_source_checks.web_sources,
        "_fetch_document",
        lambda url, title_hint, *, max_chars=6000: web_sources.SourceDocument(
            url=url, title="Official guide", text="A sufficiently long technical page body " * 5
        ),
    )
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post(
            "/api/experiences",
            json={"topic": "Python", "problem": "Check a saved documentation source."},
        )
        experience_id = created.json()["experience"]["id"]
        updated = client.patch(
            f"/api/experiences/{experience_id}",
            json={"source_evidence": [{
                "url": "https://docs.example.org/guide",
                "claim": "Official documentation",
            }]},
        )
        assert updated.status_code == 200
        version_before = updated.json()["experience"]["version"]

        rejected = client.post(
            f"/api/experiences/{experience_id}/refresh-sources",
            json={"confirm": False},
        )
        assert rejected.status_code == 422

        refreshed = client.post(
            f"/api/experiences/{experience_id}/refresh-sources",
            json={"confirm": True},
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["checks"][0]["outcome"] == "baseline"
        assert refreshed.json()["verified"] is False
        assert refreshed.json()["experience_mutated"] is False

        history = client.get(f"/api/experiences/{experience_id}/source-checks")
        assert history.status_code == 200
        assert history.json()["count"] == 1
        detail = client.get(f"/api/experiences/{experience_id}")
        assert detail.json()["experience"]["version"] == version_before



def test_feedback_api_requires_verified_record_and_can_be_cleared(tmp_path, monkeypatch):
    _isolate_app(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        created = client.post(
            "/api/experiences",
            json={"topic": "Python", "problem": "A tested retrieval issue."},
        )
        experience_id = created.json()["experience"]["id"]
        rejected = client.post(
            f"/api/experiences/{experience_id}/feedback",
            json={"rating": 1},
        )
        assert rejected.status_code == 422

        experience.update_experience(
            experience_id,
            status="verified",
            source_evidence=[{"url": "https://docs.example.org/", "claim": "Official docs"}],
            test_plan=[{"test": "regression"}],
            test_results=[{"test": "regression", "passed": True}],
            takeaway="Tested result",
            _controlled_run=True,
        )
        saved = client.post(
            f"/api/experiences/{experience_id}/feedback",
            json={"rating": -1},
        )
        assert saved.status_code == 200
        assert saved.json()["feedback"]["rating"] == -1
        assert saved.json()["changes_verification"] is False

        cleared = client.delete(f"/api/experiences/{experience_id}/feedback")
        assert cleared.status_code == 200
        assert cleared.json()["deleted"] is True
