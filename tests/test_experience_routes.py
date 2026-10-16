from fastapi.testclient import TestClient

from app import cloud_resources, db
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
