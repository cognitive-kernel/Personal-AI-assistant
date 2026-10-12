import pytest

from app import ollama


def test_ollama_embed_sends_model_and_validates_vectors(monkeypatch):
    calls = []

    def fake_request(path, payload, timeout=None):
        calls.append((path, payload, timeout))
        return {"embeddings": [[0.25, -0.5], [1.0, 0.0]]}

    monkeypatch.setattr(ollama, "_request", fake_request)
    vectors = ollama.embed(["first text", "second text"], model="test-embedding-model")

    assert vectors == [[0.25, -0.5], [1.0, 0.0]]
    assert calls[0][0] == "/api/embed"
    assert calls[0][1] == {"model": "test-embedding-model", "input": ["first text", "second text"]}
    assert calls[0][2] <= 30


def test_ollama_embed_rejects_invalid_input_and_response(monkeypatch):
    with pytest.raises(ValueError, match="between 1 and 64"):
        ollama.embed([])
    monkeypatch.setattr(ollama, "_request", lambda *_args, **_kwargs: {"embeddings": [[]]})
    with pytest.raises(ollama.OllamaError, match="empty embedding"):
        ollama.embed(["query"])


def test_ollama_embed_rejects_inconsistent_dimensions(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda *_args, **_kwargs: {"embeddings": [[1.0, 2.0], [1.0]]})
    with pytest.raises(ollama.OllamaError, match="inconsistent embedding dimensions"):
        ollama.embed(["first", "second"])
