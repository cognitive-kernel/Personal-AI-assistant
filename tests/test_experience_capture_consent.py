import pytest
from fastapi.testclient import TestClient

from app import cloud_resources, db, experience_capture_consent
from app.main import app


def _setup(tmp_path, monkeypatch):
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
    db.init_databases()


def test_experience_auto_capture_consent_is_opt_in_audited_and_revocable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    initial = experience_capture_consent.get_consent()
    assert initial["enabled"] is False
    assert initial["events"] == []

    granted = experience_capture_consent.grant_consent()
    assert granted["enabled"] is True
    assert granted["events"][0]["event_type"] == "consent_granted"

    revoked = experience_capture_consent.revoke_consent()
    assert revoked["enabled"] is False
    assert [event["event_type"] for event in revoked["events"][:2]] == [
        "consent_revoked",
        "consent_granted",
    ]
    assert experience_capture_consent.is_enabled() is False


def test_experience_auto_capture_consent_api_requires_explicit_boolean_and_loopback(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        initial = client.get("/api/experiences/auto-capture/consent")
        assert initial.status_code == 200
        assert initial.json()["consent"]["enabled"] is False

        invalid = client.post("/api/experiences/auto-capture/consent", json={"confirm": 1})
        assert invalid.status_code == 422

        granted = client.post("/api/experiences/auto-capture/consent", json={"confirm": True})
        assert granted.status_code == 200
        assert granted.json()["consent"]["enabled"] is True

        revoked = client.delete("/api/experiences/auto-capture/consent")
        assert revoked.status_code == 200
        assert revoked.json()["consent"]["enabled"] is False

    with TestClient(app, client=("192.0.2.10", 12345)) as client:
        response = client.get("/api/experiences/auto-capture/consent")
    assert response.status_code == 403
