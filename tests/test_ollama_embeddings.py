import pytest

from app import ollama
from app.ollama import OllamaError


def test_embed_uses_configured_model_and_validates_response(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "get_settings",
        lambda: {"ollama_embedding_model": "nomic-embed-text", "ollama_timeout_seconds": 300},
    )
    captured = {}

    def fake_request(path, payload, timeout):
        captured.update(path=path, payload=payload, timeout=timeout)
        return {"embeddings": [[1, 0.5], [0.25, 1]]}

    monkeypatch.setattr(ollama, "_request", fake_request)
    vectors = ollama.embed([" first ", "second"])

    assert vectors == [[1.0, 0.5], [0.25, 1.0]]
    assert captured == {
        "path": "/api/embed",
        "payload": {"model": "nomic-embed-text", "input": ["first", "second"]},
        "timeout": 8,
    }


@pytest.mark.parametrize(
    "texts",
    [[], ["  "], ["x" * 6001], ["ok"] * 33, ["ok", 4]],
)
def test_embed_rejects_invalid_inputs(texts):
    with pytest.raises(OllamaError):
        ollama.embed(texts)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"embeddings": [[1.0]]},
        {"embeddings": [[1.0], [float("nan")]]},
        {"embeddings": [[1.0], [float("inf")]]},
        {"embeddings": [[1.0], [True]]},
        {"embeddings": [[], [1.0]]},
        {"embeddings": [[1.0], [1.0, 2.0]]},
    ],
)
def test_embed_rejects_malformed_vectors(monkeypatch, response):
    monkeypatch.setattr(
        ollama,
        "get_settings",
        lambda: {"ollama_embedding_model": "test-embed", "ollama_timeout_seconds": 4},
    )
    monkeypatch.setattr(ollama, "_request", lambda *_args, **_kwargs: response)
    with pytest.raises(OllamaError):
        ollama.embed(["first", "second"])


def test_embed_rejects_invalid_model_name(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "get_settings",
        lambda: {"ollama_embedding_model": " ", "ollama_timeout_seconds": 4},
    )
    with pytest.raises(OllamaError, match="model name"):
        ollama.embed(["query"])
