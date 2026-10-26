from __future__ import annotations

import math

from app import db, learning


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    with db.learning_connection() as conn:
        job = learning.create_job("Existing database indexing lesson")
        conn.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES (?, ?, ?, 1, 'unverified', ?)""",
            (
                "Database indexes",
                "A database index improves lookup speed by allowing the database to locate rows "
                "without scanning every record.",
                job["source_kind"],
                job["id"],
            ),
        )


def test_semantic_duplicate_detects_reworded_lesson_across_topics(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    old = (
        "A database index improves lookup speed by allowing the database to locate rows "
        "without scanning every record."
    )
    paraphrase = (
        "Indexes make database searches faster because they let the engine find matching rows "
        "directly instead of reading the whole table."
    )
    calls = []

    def fake_embed(texts, model=None):
        calls.append((list(texts), model))
        vectors = {
            old: [1.0, 0.0, 0.0],
            paraphrase: [0.98, 0.18, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(learning, "ollama_embed", fake_embed)
    with db.learning_connection() as conn:
        duplicate = learning._find_semantic_duplicate_lesson(
            conn, "Database search optimization", paraphrase
        )

    assert duplicate is not None
    assert duplicate["topic"] == "Database indexes"
    assert duplicate["method"] == "embedding"
    assert duplicate["score"] >= learning.SEMANTIC_DUPLICATE_THRESHOLD
    assert calls


def test_semantically_distinct_lesson_is_not_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    old = (
        "A database index improves lookup speed by allowing the database to locate rows "
        "without scanning every record."
    )
    different = (
        "Database transactions group several operations into one atomic unit and protect "
        "consistency when concurrent users modify records."
    )

    def fake_embed(texts, model=None):
        vectors = {
            old: [1.0, 0.0, 0.0],
            different: [0.0, 1.0, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(learning, "ollama_embed", fake_embed)
    with db.learning_connection() as conn:
        duplicate = learning._find_semantic_duplicate_lesson(
            conn, "Database transactions", different
        )

    assert duplicate is None


def test_embedding_cache_prevents_recomputing_existing_lesson_vectors(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    old = (
        "A database index improves lookup speed by allowing the database to locate rows "
        "without scanning every record."
    )
    paraphrase = (
        "Indexes make database searches faster because they let the engine find matching rows "
        "directly instead of reading the whole table."
    )
    calls = []

    def fake_embed(texts, model=None):
        calls.append(list(texts))
        vectors = {
            old: [1.0, 0.0, 0.0],
            paraphrase: [0.98, 0.18, 0.0],
        }
        return [vectors[text] for text in texts]

    monkeypatch.setattr(learning, "ollama_embed", fake_embed)
    with db.learning_connection() as conn:
        first = learning._find_semantic_duplicate_lesson(conn, "Search", paraphrase)
    first_call_count = len(calls)
    with db.learning_connection() as conn:
        second = learning._find_semantic_duplicate_lesson(conn, "Search", paraphrase)

    assert first is not None and second is not None
    assert first_call_count >= 1
    assert len(calls) == first_call_count


def test_semantic_duplicate_check_fails_closed_if_embeddings_unavailable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def unavailable(*args, **kwargs):
        raise RuntimeError("embedding model unavailable")

    monkeypatch.setattr(learning, "ollama_embed", unavailable)
    with db.learning_connection() as conn:
        try:
            learning._find_semantic_duplicate_lesson(
                conn,
                "Database search",
                "Indexes make database searches faster by locating matching rows directly.",
            )
        except RuntimeError as exc:
            assert "embedding model unavailable" in str(exc)
        else:
            raise AssertionError("semantic deduplication must fail closed")
