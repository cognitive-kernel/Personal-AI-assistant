"""Shared deterministic fixtures for repository tests."""
import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_runner_machine_state_policy(monkeypatch):
    """The controller requires machine scope. Tests configure that policy explicitly."""
    monkeypatch.delenv("AUTONOMOUS_ENGINEERING_REQUIRE_MACHINE_STATE_DIR", raising=False)


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
        # Keep the test mount outside the simulated service home in both this
        # process and real child processes launched by checkpoint integration tests.
        monkeypatch.setenv("HOME", "/nonexistent-home-for-shared-state-tests")
    result = tmp_path / "shared-state"
    result.mkdir(parents=True, exist_ok=True)
    request.addfinalizer(lambda: shutil.rmtree(result, ignore_errors=True))
    return result
