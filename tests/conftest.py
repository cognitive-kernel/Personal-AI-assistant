"""Shared deterministic fixtures for repository tests."""
import os
import shutil
from pathlib import Path

import pytest

from app import autonomous_engineering_state


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



@pytest.fixture
def shared_state_tmp_path(tmp_path, request, monkeypatch):
    """Return a writable test state path outside the simulated service home."""
    if os.name != "nt":
        # Simulate a service identity whose home is unrelated to the writable
        # pytest temp mount. This keeps tests valid in read-only Docker images.
        monkeypatch.setattr(
            autonomous_engineering_state,
            "_current_home_directory",
            lambda: Path("/nonexistent-home-for-shared-state-tests"),
        )
    result = tmp_path / "shared-state"
    result.mkdir(parents=True, exist_ok=True)
    request.addfinalizer(lambda: shutil.rmtree(result, ignore_errors=True))
    return result
