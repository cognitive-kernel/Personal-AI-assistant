import json

import pytest
from fastapi.testclient import TestClient

from app import db, main, ollama


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(main, "_startup_storage_error", None)
    return TestClient(main.app)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("https://localhost:11434", "https://127.0.0.1:11434"),
        ("http://[::1]:11434", "http://[::1]:11434"),
    ],
)
def test_settings_api_accepts_supported_loopback_ollama_urls(
    settings_client, url, expected
):
    response = settings_client.patch(
        "/api/settings", json={"values": {"ollama_base_url": url}}
    )

    assert response.status_code == 200
    assert response.json()["ollama_base_url"] == expected
    assert ollama._base_url() == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:0",
        "http://127.0.0.1:11434@evil.example",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?token=unexpected",
        "http://example.com:11434",
        "https://user:password@localhost:11434",
        "http://@127.0.0.1:11434",
        "http://localhost:",
        "http://[::1]:",
        "http://[::1",
        None,
        12345,
    ],
)
def test_settings_api_rejects_invalid_ollama_urls_without_persisting(
    settings_client, url
):
    before = db.get_settings()
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"ollama_base_url": url, "chat_ollama_model": "must-not-save"}},
    )

    assert response.status_code == 422
    after = db.get_settings()
    assert after["ollama_base_url"] == before["ollama_base_url"]
    assert after["chat_ollama_model"] == before["chat_ollama_model"]



def test_settings_api_seeds_and_persists_learning_cost_preferences(settings_client):
    defaults = settings_client.get("/api/settings")
    assert defaults.status_code == 200
    assert defaults.json()["learning_model_cost_profiles"] == {}
    assert defaults.json()["learning_model_minimum_quality_score"] == 0.0
    assert defaults.json()["learning_task_budget_microusd"] is None

    profiles = {
        "local-model": {
            "provider": "local",
            "input_cost_microusd_per_1k_tokens": 0,
            "output_cost_microusd_per_1k_tokens": 0,
            "quality_score": 0.8,
            "energy_wh_per_1k_tokens": None,
        }
    }
    response = settings_client.patch(
        "/api/settings",
        json={
            "values": {
                "learning_model_cost_profiles": profiles,
                "learning_model_minimum_quality_score": 0.75,
                "learning_task_budget_microusd": 123,
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["learning_model_cost_profiles"] == profiles
    assert response.json()["learning_model_minimum_quality_score"] == 0.75
    assert response.json()["learning_task_budget_microusd"] == 123


@pytest.mark.parametrize(
    "values",
    [
        {"learning_model_cost_profiles": []},
        {"learning_model_minimum_quality_score": True},
        {"learning_model_minimum_quality_score": 1.01},
        {"learning_model_minimum_quality_score": float("nan")},
        {"learning_task_budget_microusd": -1},
        {"learning_task_budget_microusd": 1.5},
        {"learning_task_budget_microusd": 9007199254740992},
    ],
)
def test_settings_api_rejects_invalid_learning_cost_preferences_atomically(
    settings_client, values
):
    before = db.get_settings()
    payload = {"values": {**values, "chat_ollama_model": "must-not-save"}}
    response = settings_client.patch(
        "/api/settings",
        content=json.dumps(payload, allow_nan=True),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    after = db.get_settings()
    assert after["chat_ollama_model"] == before["chat_ollama_model"]
    assert after["learning_model_cost_profiles"] == before["learning_model_cost_profiles"]
    assert after["learning_model_minimum_quality_score"] == before["learning_model_minimum_quality_score"]
    assert after["learning_task_budget_microusd"] == before["learning_task_budget_microusd"]
