"""Shared deterministic fixtures for repository tests."""
import os
import tempfile
from pathlib import Path

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



@pytest.fixture
def shared_state_tmp_path(tmp_path):
    """Return a writable test state path outside HOME on POSIX hosts."""
    if os.name == "nt":
        return tmp_path / "shared-state"
    home = Path.home().resolve()
    candidate = tmp_path.resolve()
    try:
        candidate.relative_to(home)
    except ValueError:
        return candidate / "shared-state"
    relative = candidate.relative_to(home)
    shared_root = Path(tempfile.gettempdir()) / "personal-ai-shared-state-tests"
    if shared_root.resolve().is_relative_to(home):
        shared_root = Path("/var/tmp/personal-ai-shared-state-tests")
    return shared_root / relative / "shared-state"
