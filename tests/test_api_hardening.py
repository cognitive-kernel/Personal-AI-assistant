import pytest
from fastapi.testclient import TestClient

from app import db, experience
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    experience.init_experience_tables()
    with TestClient(app) as test_client:
        yield test_client


def test_public_api_cannot_downgrade_and_mutate_verified_experience(client):
    item = experience.create_experience("Python", "A verified parser edge case.")
    experience.update_experience(
        item["id"], status="verified",
        source_evidence=[{"url": "https://docs.python.org/3/library/json.html", "claim": "documented behavior"}],
        test_plan=[{"test": "contract"}],
        test_results=[{"test": "contract", "passed": True}],
        takeaway="Original verified takeaway.", _controlled_run=True,
    )

    response = client.patch(f"/api/learning/experiences/{item['id']}", json={
        "status": "needs_review", "takeaway": "tampered", "source_evidence": [],
        "test_plan": [], "test_results": [],
    })

    assert response.status_code == 422
    unchanged = experience.get_experience(item["id"])
    assert unchanged["status"] == "verified"
    assert unchanged["takeaway"] == "Original verified takeaway."
    assert unchanged["version"] == 2


def test_version_history_endpoint_and_sql_sandbox_endpoint(client):
    item = experience.create_experience("Python", "A parser edge case.")
    experience.update_experience(item["id"], status="needs_review", takeaway="First note")
    history = client.get(f"/api/learning/experiences/{item['id']}/versions")
    assert history.status_code == 200
    assert {row["version"] for row in history.json()["versions"]} == {1, 2}

    safe = client.post("/api/learning/sql-sandbox", json={"query": "SELECT COUNT(*) AS n FROM users"})
    assert safe.status_code == 200
    assert safe.json()["rows"] == [[3]]
    unsafe = client.post("/api/learning/sql-sandbox", json={"query": "DELETE FROM users"})
    assert unsafe.status_code == 422
