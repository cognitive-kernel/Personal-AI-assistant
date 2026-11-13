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



def test_ai_learning_modules_and_capacity_defaults_are_enabled_and_persistable(settings_client):
    defaults = settings_client.get("/api/settings").json()
    assert defaults["ai_learning_enabled"] is True
    assert defaults["ai_learning_knowledge_enabled"] is True
    assert defaults["ai_learning_retrieval_enabled"] is True
    assert defaults["ai_learning_browser_enabled"] is True
    assert defaults["ai_learning_llm_provider"] == "ollama"
    assert defaults["ai_learning_ollama_model"]
    assert defaults["ai_learning_source_max_chars"] == 100000
    assert defaults["ai_learning_min_complete_lesson_words"] == 1200
    assert defaults["ai_learning_browser_poll_interval_ms"] == 1000
    assert defaults["ai_learning_browser_control_poll_interval_ms"] == 100
    assert defaults["ai_learning_browser_lease_seconds"] == 90
    assert defaults["ai_learning_browser_lease_heartbeat_seconds"] == 15
    assert defaults["ai_learning_chrome_path"] == ""
    assert defaults["ai_learning_browser_profile"] == "data/ai_learning_browser_profile"
    assert defaults["ai_learning_max_context_relaunches"] == 1
    assert defaults["ai_learning_stop_stability_seconds"] == 45
    assert defaults["ai_learning_last_stop_stability_seconds"] == 25
    assert defaults["basic_learning_enabled"] is True
    assert defaults["topic_learning_enabled"] is True
    assert defaults["experience_system_enabled"] is True
    assert defaults["sandbox_enabled"] is True
    assert defaults["self_improvement_enabled"] is True
    assert defaults["topic_learning_llm_provider"] == "ollama"
    assert defaults["topic_learning_source_max_chars"] == 100000
    assert defaults["research_plan_ollama_num_ctx"] >= 512
    assert defaults["research_plan_ollama_num_thread"] >= 0
    assert defaults["research_plan_ollama_timeout_seconds"] >= 5
    assert defaults["research_plan_model_keep_alive"] == "0"
    assert defaults["research_plan_source_max_chars"] == 16000
    assert defaults["research_plan_max_output_tokens"] == 700
    assert defaults["factual_review_ollama_num_ctx"] == 4096
    assert defaults["factual_review_ollama_timeout_seconds"] == 120
    assert defaults["factual_review_source_max_chars"] == 16000
    assert defaults["factual_review_max_output_tokens"] == 500
    assert defaults["ai_learning_ollama_model"]
    assert defaults["ai_learning_ollama_num_ctx"] >= 512
    assert defaults["gpu_enabled"] is True
    assert defaults["ai_learning_lesson_batch_size"] == 20
    assert defaults["ai_learning_embedding_input_max_chars"] == 6000
    assert defaults["ai_learning_title_duplicate_threshold"] == 0.985
    assert defaults["ai_learning_content_duplicate_threshold"] == 0.90

    values = {
        "ai_learning_enabled": False,
        "experience_system_enabled": False,
        "ai_learning_retrieval_enabled": False,
        "ai_learning_browser_enabled": False,
        "sandbox_enabled": False,
        "self_improvement_enabled": False,
        "sandbox_docker_executable": "/opt/docker/bin/docker",
        "sandbox_python_image": "python:3.11-slim@sha256:" + "a" * 64,
        "sandbox_timeout_seconds": 90,
        "sandbox_test_timeout_seconds": 600,
        "sandbox_max_output_bytes": 64000,
        "self_improvement_repo_root": "/workspace/project",
        "self_improvement_github_repository": "owner/repository",
        "self_improvement_github_base_branch": "develop",
        "self_improvement_github_token_env": "CUSTOM_GITHUB_TOKEN",
        "ai_learning_lesson_batch_size": 5,
        "ai_learning_chunk_target_chars": 2400,
        "ai_learning_chunk_overlap_chars": 240,
        "ai_learning_embedding_batch_size": 32,
        "ai_learning_embedding_input_max_chars": 12000,
        "ai_learning_title_duplicate_threshold": 0.95,
        "ai_learning_content_duplicate_threshold": 0.92,
        "ai_learning_response_timeout_seconds": 2400,
        "ai_learning_max_lesson_continuations": 6,
        "ai_learning_stability_seconds": 30,
        "ai_learning_browser_timeout_seconds": 600,
        "ai_learning_browser_poll_interval_ms": 1500,
        "ai_learning_browser_control_poll_interval_ms": 200,
        "ai_learning_browser_lease_seconds": 120,
        "ai_learning_browser_lease_heartbeat_seconds": 20,
        "ai_learning_chrome_path": "/usr/bin/google-chrome",
        "ai_learning_browser_profile": "/tmp/ai-learning-profile",
        "ai_learning_max_retries": 3,
        "ai_learning_min_complete_lesson_words": 2400,
        "ai_learning_max_context_relaunches": 2,
        "ai_learning_stop_stability_seconds": 60,
        "ai_learning_last_stop_stability_seconds": 35,
        "ai_learning_llm_provider": "openai_compatible",
        "ai_learning_llm_base_url": "https://api.openai.com/v1",
        "ai_learning_llm_api_key_env": "OPENAI_API_KEY",
        "ai_learning_ollama_model": "gpt-5-test",
        "ai_learning_source_max_chars": 120000,
        "topic_learning_llm_provider": "openai_compatible",
        "topic_learning_llm_base_url": "https://api.openai.com/v1",
        "topic_learning_llm_api_key_env": "OPENAI_API_KEY",
        "topic_learning_ollama_model": "gpt-5-topic-test",
        "topic_learning_source_max_chars": 150000,
        "research_plan_ollama_num_ctx": 8192,
        "research_plan_ollama_num_thread": 6,
        "research_plan_ollama_timeout_seconds": 180,
        "research_plan_model_keep_alive": "5m",
        "research_plan_source_max_chars": 32000,
        "research_plan_max_output_tokens": 1200,
        "factual_review_ollama_num_ctx": 8192,
        "factual_review_ollama_num_thread": 4,
        "factual_review_ollama_timeout_seconds": 90,
        "factual_review_model_keep_alive": "1m",
        "factual_review_source_max_chars": 24000,
        "factual_review_max_output_tokens": 900,
        "ai_learning_ollama_num_ctx": 8192,
        "ai_learning_ollama_num_thread": 4,
        "ai_learning_ollama_timeout_seconds": 120,
        "ai_learning_model_keep_alive": "5m",
    }
    response = settings_client.patch("/api/settings", json={"values": values})
    assert response.status_code == 200
    for key, value in values.items():
        assert response.json()[key] == value
    assert settings_client.get("/api/settings").json()["ai_learning_enabled"] is False


@pytest.mark.parametrize(
    "values",
    [
        {"ai_learning_enabled": "false"},
        {"ai_learning_knowledge_enabled": "false"},
        {"experience_system_enabled": "false"},
        {"ai_learning_retrieval_enabled": 1},
        {"ai_learning_browser_enabled": None},
        {"research_plan_max_output_tokens": 0},
        {"research_plan_max_output_tokens": 8193},
        {"factual_review_max_output_tokens": 0},
        {"factual_review_source_max_chars": 999},
        {"factual_review_ollama_num_ctx": 511},
        {"sandbox_enabled": "true"},
        {"self_improvement_enabled": 1},
        {"sandbox_docker_executable": "relative/docker"},
        {"sandbox_python_image": "python:latest"},
        {"sandbox_test_image": "sha256:bad"},
        {"sandbox_node_image": "node:latest"},
        {"sandbox_rust_image": "rust:latest"},
        {"self_improvement_repo_root": "relative/project"},
        {"self_improvement_github_repository": "not-a-repository"},
        {"self_improvement_github_base_branch": "../unsafe"},
        {"sandbox_timeout_seconds": 121},
        {"sandbox_max_output_bytes": 0},
        {"self_improvement_github_token_env": "not-valid"},
        {"ai_learning_lesson_batch_size": 21},
        {"ai_learning_chunk_target_chars": 255},
        {"ai_learning_chunk_overlap_chars": 2001},
        {"ai_learning_embedding_batch_size": 0},
        {"ai_learning_response_timeout_seconds": 29},
        {"ai_learning_max_lesson_continuations": 21},
        {"ai_learning_stability_seconds": 0},
        {"ai_learning_chunk_target_chars": 1000, "ai_learning_chunk_overlap_chars": 1000},
        {"ai_learning_browser_timeout_seconds": 9},
        {"ai_learning_browser_poll_interval_ms": 99},
        {"ai_learning_browser_control_poll_interval_ms": 1001},
        {"ai_learning_browser_lease_seconds": 29},
        {"ai_learning_browser_lease_heartbeat_seconds": 31},
        {"ai_learning_chrome_path": "relative/chrome"},
        {"ai_learning_browser_lease_seconds": 30, "ai_learning_browser_lease_heartbeat_seconds": 15},
        {"ai_learning_max_retries": 6},
        {"ai_learning_embedding_input_max_chars": 255},
        {"ai_learning_title_duplicate_threshold": 1.01},
        {"ai_learning_content_duplicate_threshold": float("nan")},
        {"ai_learning_min_complete_lesson_words": 99},
        {"ai_learning_max_context_relaunches": 4},
        {"ai_learning_stop_stability_seconds": 181},
        {"ai_learning_last_stop_stability_seconds": 0},
    ],
)
def test_ai_learning_settings_reject_invalid_values_atomically(settings_client, values):
    before = db.get_settings()
    response = settings_client.patch(
        "/api/settings",
        json={"values": {**values, "chat_ollama_model": "must-not-save"}},
    )
    assert response.status_code == 422
    after = db.get_settings()
    assert after["chat_ollama_model"] == before["chat_ollama_model"]



def test_model_catalog_endpoint_uses_requested_capability_provider(settings_client, monkeypatch):
    monkeypatch.setattr(ollama, "list_provider_models", lambda purpose: {
        "provider": "openai_compatible",
        "models": [{"name": purpose + "-model", "size": None}],
    })
    response = settings_client.get("/api/models?purpose=ai_learning")
    assert response.status_code == 200
    assert response.json()["provider"] == "openai_compatible"
    assert response.json()["models"][0]["name"] == "ai_learning-model"


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
        "recommended_context_tokens": 32768,
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



def test_settings_api_rejects_context_change_without_hardware_safe_estimate(settings_client, monkeypatch):
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name,
        "maximum_context_tokens": 65536,
        "recommended_context_tokens": None,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_num_ctx": 16384}},
    )
    assert response.status_code == 422
    assert "hardware-safe context cannot be calculated" in response.json()["detail"]


def test_settings_api_clamps_manual_context_to_hardware_safe_estimate(settings_client, monkeypatch):
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name,
        "maximum_context_tokens": 65536,
        "recommended_context_tokens": 4096,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_num_ctx": 16384}},
    )
    assert response.status_code == 200
    assert response.json()["chat_ollama_num_ctx"] == 4096
    assert response.json()["ollama_num_ctx"] == 4096


def test_settings_api_does_not_invent_context_when_model_memory_estimate_is_missing(settings_client, monkeypatch):
    before = db.get_settings()
    monkeypatch.setattr(ollama, "model_details", lambda name, context_tokens=None: {
        "name": name,
        "maximum_context_tokens": 65536,
        "recommended_context_tokens": None,
    })
    response = settings_client.patch(
        "/api/settings",
        json={"values": {"chat_ollama_model": "unknown-memory-model:latest"}},
    )
    assert response.status_code == 200
    after = db.get_settings()
    assert after["chat_ollama_model"] == "unknown-memory-model:latest"
    assert after["chat_ollama_num_ctx"] == before["chat_ollama_num_ctx"]


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

 
def test_ai_learning_config_reads_persisted_settings(settings_client):
    from app.ai_learning import config

    assert config.is_enabled() is True
    assert config.retrieval_is_enabled() is True
    assert config.browser_is_enabled() is True
    assert config.max_topics_per_run() == 20

    db.update_settings({
        "ai_learning_enabled": False,
        "ai_learning_retrieval_enabled": False,
        "ai_learning_browser_enabled": False,
        "ai_learning_lesson_batch_size": 3,
    })
    assert config.is_enabled() is False
    assert config.retrieval_is_enabled() is False
    assert config.browser_is_enabled() is False
    assert config.max_topics_per_run() == 3
