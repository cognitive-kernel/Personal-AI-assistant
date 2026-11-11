import io
import json

import pytest

from app import ollama


class _FakeOpener:
    def __init__(self, payload, captured):
        self.payload = payload
        self.captured = captured

    def open(self, request, timeout):
        self.captured["url"] = request.full_url
        self.captured["authorization"] = request.get_header("Authorization")
        self.captured["payload"] = json.loads(request.data.decode("utf-8"))
        self.captured["timeout"] = timeout
        return io.BytesIO(json.dumps(self.payload).encode("utf-8"))


def test_openai_compatible_chat_uses_selected_model_and_environment_api_key(monkeypatch):
    captured = {}
    monkeypatch.setenv("TEST_LLM_TOKEN", "secret-value")
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "learning_llm_base_url": "https://llm.example.test/v1",
            "learning_llm_api_key_env": "TEST_LLM_TOKEN",
        },
    )
    monkeypatch.setattr(
        ollama.urllib.request, "build_opener",
        lambda *_args, **_kwargs: _FakeOpener(
            {"choices": [{"message": {"content": "lesson response"}}]}, captured
        ),
    )

    result = ollama._openai_compatible_chat(
        "Explain SQL Server", [{"role": "user", "content": "Earlier context"}],
        "You are a teacher.", purpose="learning", model="custom-model",
        timeout=90, num_predict=1200,
    )

    assert result == "lesson response"
    assert captured["url"] == "https://llm.example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-value"
    assert captured["payload"]["model"] == "custom-model"
    assert captured["payload"]["max_tokens"] == 1200
    assert captured["payload"]["messages"][0] == {
        "role": "system", "content": "You are a teacher."
    }
    assert captured["payload"]["messages"][1]["content"] == "Earlier context"


def test_openai_compatible_chat_supports_local_endpoint_without_api_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "programming_llm_base_url": "http://127.0.0.1:1234/v1",
            "programming_llm_api_key_env": "",
        },
    )
    monkeypatch.setattr(
        ollama.urllib.request, "build_opener",
        lambda *_args, **_kwargs: _FakeOpener(
            {"choices": [{"message": {"content": "ok"}}]}, captured
        ),
    )

    result = ollama._openai_compatible_chat(
        "Write code", None, None, purpose="programming", model="local-coder",
        timeout=30, num_predict=None,
    )

    assert result == "ok"
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["authorization"] is None


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "https://user:password@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/../admin",
        "http://127.0.0.1:0/v1",
    ],
)
def test_openai_compatible_base_url_rejects_unsafe_urls(url):
    with pytest.raises(ollama.OllamaError):
        ollama.normalize_openai_compatible_base_url(url)


def test_openai_compatible_base_url_accepts_https_and_loopback_http():
    assert (
        ollama.normalize_openai_compatible_base_url("https://api.openai.com/v1/")
        == "https://api.openai.com/v1"
    )
    assert (
        ollama.normalize_openai_compatible_base_url("http://localhost:1234/v1")
        == "http://127.0.0.1:1234/v1"
    )


def test_openai_compatible_chat_rejects_missing_api_key_environment_variable(monkeypatch):
    monkeypatch.delenv("MISSING_LLM_TOKEN", raising=False)
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "chat_llm_base_url": "https://llm.example.test/v1",
            "chat_llm_api_key_env": "MISSING_LLM_TOKEN",
        },
    )

    with pytest.raises(ollama.OllamaError, match="is not set"):
        ollama._openai_compatible_chat(
            "hello", None, None, purpose="chat", model="model",
            timeout=20, num_predict=100,
        )


def test_chat_routes_to_selected_openai_compatible_provider(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "chat_llm_provider": "openai_compatible",
            "chat_ollama_model": "selected-chat-model",
            "chat_ollama_timeout_seconds": 45,
        },
    )
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {})
    monkeypatch.setattr(
        ollama, "_openai_compatible_chat",
        lambda *args, **kwargs: captured.update(kwargs) or "provider reply",
    )

    result = ollama.chat("hello", purpose="chat", num_predict=321)

    assert result == "provider reply"
    assert captured["purpose"] == "chat"
    assert captured["model"] == "selected-chat-model"
    assert captured["num_predict"] == 321


def test_openai_compatible_embeddings_preserve_input_order(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "embedding_llm_base_url": "http://127.0.0.1:1234/v1",
            "embedding_llm_api_key_env": "",
        },
    )
    monkeypatch.setattr(
        ollama.urllib.request, "build_opener",
        lambda *_args, **_kwargs: _FakeOpener(
            {
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
            captured,
        ),
    )

    vectors = ollama._openai_compatible_embeddings(
        ["first", "second"], model="embed-model", timeout=20
    )

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"] == "http://127.0.0.1:1234/v1/embeddings"
    assert captured["payload"] == {"model": "embed-model", "input": ["first", "second"]}


def test_embed_uses_selected_compatible_provider(monkeypatch):
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "embedding_llm_provider": "openai_compatible",
            "ollama_embedding_model": "remote-embedding",
            "ollama_timeout_seconds": 30,
        },
    )
    monkeypatch.setattr(
        ollama, "_openai_compatible_embeddings",
        lambda texts, **kwargs: [[float(len(text)), 1.0] for text in texts],
    )
    monkeypatch.setattr(
        ollama, "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Ollama must not be called")),
    )

    assert ollama.embed(["alpha", "beta"]) == [[5.0, 1.0], [4.0, 1.0]]


def test_direct_learning_chat_requests_route_through_selected_provider(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "learning_llm_provider": "openai_compatible",
            "learning_ollama_model": "remote-learning-model",
            "learning_ollama_timeout_seconds": 75,
        },
    )
    monkeypatch.setattr(
        ollama, "_openai_compatible_chat",
        lambda message, history, system_prompt, **kwargs: captured.update({
            "message": message, "history": history, "system_prompt": system_prompt, **kwargs
        }) or '{"lesson":"ok"}',
    )

    result = ollama._request(
        "/api/chat",
        {
            "model": "remote-learning-model",
            "format": "json",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "serialized lesson prompt"},
            ],
            "options": {"num_predict": 3900},
        },
        timeout=75,
        purpose="learning",
    )

    assert result["message"]["content"] == '{"lesson":"ok"}'
    assert captured["purpose"] == "learning"
    assert captured["model"] == "remote-learning-model"
    assert captured["num_predict"] == 3900
    assert captured["response_format"] == "json"
    assert captured["message"] == "serialized lesson prompt"


def test_runtime_diagnostics_does_not_contact_ollama_for_compatible_provider(monkeypatch):
    monkeypatch.setattr(
        ollama, "get_settings",
        lambda: {
            "learning_llm_provider": "openai_compatible",
            "learning_ollama_num_ctx": 32768,
        },
    )
    monkeypatch.setattr(ollama, "detect_hardware", lambda: {
        "logical_cpu_count": 8, "available_ram_gb": 16, "total_ram_gb": 32,
    })
    monkeypatch.setattr(
        ollama, "_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Ollama must not be contacted")),
    )

    result = ollama.runtime_diagnostics("remote-model", 32768, "learning")

    assert result["provider"] == "openai_compatible"
    assert result["runtime_limits"]["num_ctx"] == 32768
    assert result["selected_model"]["name"] == "remote-model"
    assert result["selected_model"]["memory_estimate"] is None
