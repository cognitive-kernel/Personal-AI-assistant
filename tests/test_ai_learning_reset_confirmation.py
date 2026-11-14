from fastapi.testclient import TestClient

from app.ai_learning import browser_automation, config, routes
from app.main import app


def test_learning_data_reset_requires_exact_server_side_confirmation(monkeypatch):
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    monkeypatch.setattr(routes, "initialize_store", lambda: None)

    reset_calls = []
    monkeypatch.setattr(
        browser_automation,
        "reset_learning_data",
        lambda: reset_calls.append(True) or {"reset": True},
    )

    client = TestClient(
        app,
        client=("127.0.0.1", 54321),
        base_url="http://127.0.0.1:8000",
    )

    missing = client.post("/api/ai-learning/reset-learning-data")
    assert missing.status_code == 422

    legacy_confirmation = client.post(
        "/api/ai-learning/reset-learning-data",
        json={"confirmation": "RESET"},
    )
    assert legacy_confirmation.status_code == 422

    unexpected_field = client.post(
        "/api/ai-learning/reset-learning-data",
        json={
            "confirmation": "RESET AI LEARNING DATA",
            "unexpected": True,
        },
    )
    assert unexpected_field.status_code == 422
    assert reset_calls == []

    confirmed = client.post(
        "/api/ai-learning/reset-learning-data",
        json={"confirmation": "RESET AI LEARNING DATA"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {"reset": True}
    assert reset_calls == [True]


def test_learning_data_reset_ui_sends_the_server_confirmation():
    from pathlib import Path

    source = Path("app/static/ai-learning.js").read_text(encoding="utf-8")
    assert 'confirmation !== "RESET AI LEARNING DATA"' in source
    assert (
        'jsonOptions("POST", {confirmation:"RESET AI LEARNING DATA"})'
        in source
    )
