from __future__ import annotations

import pytest

from app.ai_learning import integration, service
from app.ai_learning.db import initialize_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()
    # Keep this regression test deterministic and independent of Ollama.
    monkeypatch.setattr(
        service,
        "ollama_embed",
        lambda texts, model: [\n            [1.0, 0.0] if "approved-chat-context-token" in text else [0.0, 1.0]\n            for text in texts\n        ],
    )


def test_chat_retrieval_gate_off_returns_no_learning_context(isolated_store, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED", "0")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_ENABLED", "1")
    assert integration.retrieve_context("approved context needle") == []


def test_main_chat_adapter_never_returns_unpublished_content(isolated_store, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_ENABLED", "1")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_ENABLED", "1")

    course = service.create_course(title="Retrieval publication boundary")
    topic = service.add_topic(course["id"], "Approved topic")
    service.decide_topics([topic["id"]], "approved")

    approved_lesson = service.create_lesson(topic["id"], "Published reference")
    approved_version = service.add_content_version(
        approved_lesson["id"],
        "# Published reference\n\napproved-chat-context-token is safe for retrieval.",
    )
    service.review_content(approved_version["id"], "reviewed")
    service.publish_content(approved_version["id"])

    draft_lesson = service.create_lesson(topic["id"], "Draft reference")
    service.add_content_version(
        draft_lesson["id"],
        "# Draft reference\n\nnever-return-this-draft-token before explicit publication.",
    )

    items = integration.retrieve_context("approved-chat-context-token never-return-this-draft-token")
    combined = "\n".join(item["text"] for item in items)
    assert "approved-chat-context-token" in combined
    assert "never-return-this-draft-token" not in combined
    assert all(item.get("content_version_id") == approved_version["id"] for item in items)
