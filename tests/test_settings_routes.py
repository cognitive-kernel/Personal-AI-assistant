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
    response = settings_client.patch("/api/settings", json={"values": {"ollama_base_url": url}})

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
