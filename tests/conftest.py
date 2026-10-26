"""Shared deterministic fixtures for tests that exercise AI-learning workflows."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_ai_learning_embedding_dependencies(monkeypatch):
    """Keep unit tests offline and deterministic; individual tests can override either stub."""
    from app.ai_learning import service

    monkeypatch.setattr(service, "get_settings", lambda: {})

    def deterministic_embeddings(texts, model=None):
        size = len(texts)
        return [[1.0 if index == column else 0.0 for column in range(size)]
                for index, _text in enumerate(texts)]

    monkeypatch.setattr(service, "ollama_embed", deterministic_embeddings)
