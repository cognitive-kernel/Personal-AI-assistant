from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.ai_learning import integration, service
from app.ai_learning.db import connection, initialize_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_LEARNING_DB_PATH", str(tmp_path / "ai_learning.db"))
    initialize_store()
    # Keep this regression test deterministic and independent of Ollama.
    monkeypatch.setattr(
        service,
        "ollama_embed",
        lambda texts, model: [[1.0, 0.0] if "approved-chat-context-token" in text else [0.0, 1.0] for text in texts],
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


def test_published_knowledge_is_retrievable_after_a_new_process(
    isolated_store, monkeypatch, tmp_path
):
    database_path = tmp_path / "ai_learning.db"
    monkeypatch.setenv("AI_LEARNING_ENABLED", "1")
    monkeypatch.setenv("AI_LEARNING_RETRIEVAL_ENABLED", "1")

    course = service.create_course(title="Durable retrieval")
    topic = service.add_topic(course["id"], "Approved durable topic")
    service.decide_topics([topic["id"]], "approved")

    published_lesson = service.create_lesson(topic["id"], "Published durable lesson")
    published_version = service.add_content_version(
        published_lesson["id"],
        "# Durable retrieval marker\n\n"
        "The durable retrieval marker proves that published knowledge survives "
        "a fresh application process and remains available to a later chat request.",
    )
    service.review_content(published_version["id"], "reviewed")
    service.publish_content(published_version["id"])

    draft_lesson = service.create_lesson(topic["id"], "Unpublished durable lesson")
    service.add_content_version(
        draft_lesson["id"],
        "# Unpublished process restart token\n\n"
        "This unpublished process restart token must never be returned.",
    )

    with connection() as conn:
        traces_before = conn.execute("SELECT COUNT(*) FROM query_traces").fetchone()[0]

    script = (
        "import json; "
        "from app.ai_learning.integration import retrieve_context; "
        "print(json.dumps(retrieve_context('durable retrieval marker'), ensure_ascii=False))"
    )
    env = os.environ.copy()
    env["AI_LEARNING_DB_PATH"] = str(database_path)
    env["AI_LEARNING_ENABLED"] = "1"
    env["AI_LEARNING_RETRIEVAL_ENABLED"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    items = json.loads(completed.stdout)
    combined = "\n".join(item["text"] for item in items)
    assert items
    assert "durable retrieval marker" in combined
    assert "unpublished process restart token" not in combined
    assert all(item["content_version_id"] == published_version["id"] for item in items)
    with connection() as conn:
        traces_after = conn.execute("SELECT COUNT(*) FROM query_traces").fetchone()[0]
    assert traces_after == traces_before + 1

