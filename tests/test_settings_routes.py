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
        {"learning_model_minimum_quality_score": 10**1000},
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


@pytest.mark.parametrize(
    ("key", "invalid_value"),
    [
        ("ollama_num_ctx", 2048.5),
        ("chat_max_concurrent_jobs", "2"),
        ("learning_source_max_chars", 1000.25),
    ],
)
def test_settings_api_rejects_non_integer_numeric_settings_atomically(
    settings_client, key, invalid_value
):
    before = db.get_settings()
    response = settings_client.patch(
        "/api/settings",
        json={
            "values": {
                key: invalid_value,
                "chat_ollama_model": "must-not-save",
            }
        },
    )

    assert response.status_code == 422
    after = db.get_settings()
    assert after[key] == before[key]
    assert after["chat_ollama_model"] == before["chat_ollama_model"]


def test_settings_api_rejects_context_above_selected_model_limit_atomically(settings_client, monkeypatch):
    before = db.get_settings()
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name, "maximum_context_tokens": 32768,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "local:8b", "chat_ollama_num_ctx": 65536}},
    )
    assert response.status_code == 422
    assert "32768" in response.json()["detail"]
    after = db.get_settings()
    assert after["chat_ollama_model"] == before["chat_ollama_model"]
    assert after["chat_ollama_num_ctx"] == before["chat_ollama_num_ctx"]


def test_settings_api_accepts_context_below_selected_model_limit(settings_client, monkeypatch):
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name, "maximum_context_tokens": 65536,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "local:8b", "chat_ollama_num_ctx": 16384}},
    )
    assert response.status_code == 200
    assert response.json()["chat_ollama_num_ctx"] == 16384


def test_settings_api_rejects_context_when_model_limit_cannot_be_verified(settings_client, monkeypatch):
    before = db.get_settings()
    db.update_settings({"chat_ollama_model": "existing-model:7b", "chat_ollama_num_ctx": 2048})
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name, "maximum_context_tokens": None,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_num_ctx": 4096}},
    )
    assert response.status_code == 422
    assert "metadata" in response.json()["detail"]
    assert db.get_settings()["chat_ollama_num_ctx"] == before["chat_ollama_num_ctx"]


def test_settings_api_auto_reduces_context_when_new_model_has_a_smaller_limit(settings_client, monkeypatch):
    db.update_settings({"chat_ollama_model": "existing-model:7b", "chat_ollama_num_ctx": 2048})
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name,
        "maximum_context_tokens": 1024 if name == "small-model:1b" else 32768,
        "recommended_context_tokens": 1024,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "small-model:1b"}},
    )
    assert response.status_code == 200
    after = db.get_settings()
    assert after["chat_ollama_model"] == "small-model:1b"
    assert after["chat_ollama_num_ctx"] == 1024
    assert after["ollama_num_ctx"] == 1024

def test_model_details_endpoint_exposes_context_and_selected_context_estimate(settings_client, monkeypatch):
    captured = {}
    def fake_details(name, context_tokens=None):
        captured.update(name=name, context_tokens=context_tokens)
        return {
            "name": name,
            "maximum_context_tokens": 65536,
            "memory_estimate": {"context_tokens": context_tokens, "total_ram_gib_float16": 12.5},
        }
    monkeypatch.setattr(ollama, "model_details", fake_details)
    response = settings_client.get("/api/model-details?name=local%3A8b&context_tokens=16384")
    assert response.status_code == 200
    assert response.json()["maximum_context_tokens"] == 65536
    assert response.json()["memory_estimate"]["context_tokens"] == 16384
    assert captured == {"name": "local:8b", "context_tokens": 16384}


def test_hardware_diagnostics_endpoint_returns_cpu_ram_gpu_and_ollama_state(settings_client, monkeypatch):
    monkeypatch.setattr(ollama, "runtime_diagnostics", lambda model=None, context_tokens=None, purpose="chat": {
        "hardware": {
            "logical_cpu_count": 12,
            "cpu_utilization_percent": 25,
            "cpu_available_percent": 75,
            "available_ram_gb": 12,
            "gpu_available": True,
            "gpu_devices": [{"name": "Intel UHD", "memory_free_mb": None}],
        },
        "ollama_running_models": [{"name": model, "context_length": context_tokens, "size_vram_bytes": 0, "processor": "CPU"}] if model else [],
    })
    response = settings_client.get("/api/hardware/diagnostics?model=local%3A8b&context_tokens=8192")
    assert response.status_code == 200
    assert response.json()["hardware"]["gpu_devices"][0]["memory_free_mb"] is None
    assert response.json()["ollama_running_models"][0]["processor"] == "CPU"



def test_settings_api_auto_selects_context_when_model_changes_without_manual_value(settings_client, monkeypatch):
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name,
        "maximum_context_tokens": 65536,
        "recommended_context_tokens": 32768,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "local:8b"}},
    )
    assert response.status_code == 200
    assert response.json()["chat_ollama_model"] == "local:8b"
    assert response.json()["chat_ollama_num_ctx"] == 32768
    assert response.json()["ollama_num_ctx"] == 32768


def test_settings_api_preserves_manual_model_selection_when_context_is_unchanged(settings_client, monkeypatch):
    before = db.get_settings()
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name, "maximum_context_tokens": None,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "manual-model:latest"}},
    )
    assert response.status_code == 200
    after = db.get_settings()
    assert after["chat_ollama_model"] == "manual-model:latest"
    assert after["chat_ollama_num_ctx"] == before["chat_ollama_num_ctx"]


def test_settings_api_does_not_invent_context_when_safe_recommendation_is_unavailable(settings_client, monkeypatch):
    db.update_settings({"chat_ollama_model": "existing-model:7b", "chat_ollama_num_ctx": 2048})
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name,
        "maximum_context_tokens": 32768,
        "recommended_context_tokens": None,
    })

    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "new-model:8b"}},
    )

    assert response.status_code == 200
    after = db.get_settings()
    assert after["chat_ollama_model"] == "new-model:8b"
    assert after["chat_ollama_num_ctx"] == 2048


def test_settings_api_switches_learning_to_openai_compatible_without_ollama_metadata(
    settings_client, monkeypatch
):
    monkeypatch.setattr(
        ollama, "model_details",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Ollama metadata must not be queried")),
    )
    response = settings_client.patch(
        "/api/settings",
        json={"values": {
            "learning_llm_provider": "openai_compatible",
            "learning_llm_base_url": "http://127.0.0.1:1234/v1",
            "learning_llm_api_key_env": "",
            "learning_ollama_model": "local-gpt-oss",
            "learning_ollama_num_ctx": 32768,
        }},
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved["learning_llm_provider"] == "openai_compatible"
    assert saved["learning_llm_base_url"] == "http://127.0.0.1:1234/v1"
    assert saved["learning_ollama_model"] == "local-gpt-oss"
    assert saved["learning_ollama_num_ctx"] == 32768


def test_settings_api_rejects_plaintext_remote_llm_endpoint_atomically(settings_client):
    before = db.get_settings()
    response = settings_client.patch(
        "/api/settings",
        json={"values": {
            "learning_llm_provider": "openai_compatible",
            "learning_llm_base_url": "http://llm.example.test/v1",
            "learning_ollama_model": "must-not-save",
        }},
    )

    assert response.status_code == 422
    after = db.get_settings()
    assert after["learning_llm_provider"] == before["learning_llm_provider"]
    assert after["learning_ollama_model"] == before["learning_ollama_model"]


def test_settings_api_rejects_api_key_values_and_invalid_provider(settings_client):
    for values in (
        {"learning_llm_provider": "unknown-provider"},
        {"learning_llm_api_key_env": "sk-this-is-a-secret-not-an-env-name"},
    ):
        response = settings_client.patch("/api/settings", json={"values": values})
        assert response.status_code == 422


def test_temporary_settings_apply_immediately_without_persisting(settings_client):
    before = db.get_settings()
    response = settings_client.patch(
        "/api/settings",
        json={
            "temporary": True,
            "values": {
                "learning_llm_provider": "openai_compatible",
                "learning_llm_base_url": "http://127.0.0.1:1234/v1",
                "learning_ollama_model": "temporary-model",
            },
        },
    )

    assert response.status_code == 200
    assert db.get_settings()["learning_llm_provider"] == "openai_compatible"
    assert db.get_settings()["learning_ollama_model"] == "temporary-model"
    with db.settings_connection() as conn:
        persisted_provider = json.loads(
            conn.execute(
                "SELECT value_json FROM settings WHERE key='learning_llm_provider'"
            ).fetchone()[0]
        )
        persisted_model = json.loads(
            conn.execute(
                "SELECT value_json FROM settings WHERE key='learning_ollama_model'"
            ).fetchone()[0]
        )
    assert persisted_provider == before["learning_llm_provider"]
    assert persisted_model == before["learning_ollama_model"]

    db.update_settings({
        "learning_llm_provider": before["learning_llm_provider"],
        "learning_llm_base_url": before["learning_llm_base_url"],
        "learning_ollama_model": before["learning_ollama_model"],
    })
