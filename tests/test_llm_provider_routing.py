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
        == "http://localhost:1234/v1"
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
