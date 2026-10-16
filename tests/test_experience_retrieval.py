import json

from app import db, experience, ollama
from app.db import learning_connection
from app.ollama import OllamaError


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _verified(topic, problem, takeaway):
    item = experience.create_experience(topic, problem, "Check the measured cause.")
    return experience.update_experience(
        item["id"],
        status="verified",
        source_evidence=[{
            "url": "https://docs.example.org/reference",
            "claim": "The documented behavior relevant to this experience.",
        }],
        test_plan=[{"test": "reproducible regression"}],
        test_results=[{"test": "reproducible regression", "passed": True, "actual": "passed"}],
        takeaway=takeaway,
        _controlled_run=True,
    )


def test_hybrid_search_finds_semantic_match_without_lexical_overlap(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    relevant = _verified(
        "Database optimizer",
        "Plan regression under concurrent writes.",
        "Inspect execution plans and row estimates.",
    )
    unrelated = _verified(
        "Video codec",
        "Frame rendering latency.",
        "Measure frame quality.",
    )
    calls = []

    def fake_embed(texts, model=None):
        calls.append((list(texts), model))
        vectors = []
        for text in texts:
            if text.startswith("semantic query") or "Database optimizer" in text:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors

    monkeypatch.setattr(ollama, "embed", fake_embed)
    results = experience.search_relevant_experiences("semantic query about throughput")

    assert [item["id"] for item in results] == [relevant["id"]]
    assert unrelated["id"] not in [item["id"] for item in results]
    assert len(calls) == 2  # one query batch and one batch for uncached experiences
    with learning_connection() as connection:
        cached = connection.execute(
            "SELECT experience_id, experience_version, dimensions, embedding_json "
            "FROM learning_experience_embeddings ORDER BY experience_id"
        ).fetchall()
    assert len(cached) == 2
    assert all(row["dimensions"] == 2 for row in cached)
    assert all(len(json.loads(row["embedding_json"])) == 2 for row in cached)

    experience.search_relevant_experiences("semantic query about throughput")
    assert len(calls) == 3  # query re-embedded, experience vectors reused from cache


def test_hybrid_search_falls_back_to_lexical_when_embeddings_are_unavailable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    relevant = _verified(
        "Python JSON parser",
        "Parser rejects an empty payload.",
        "Validate input before parsing.",
    )

    def unavailable(*_args, **_kwargs):
        raise OllamaError("embedding model unavailable")

    monkeypatch.setattr(ollama, "embed", unavailable)
    results = experience.search_relevant_experiences("Python JSON parser")
    assert [item["id"] for item in results] == [relevant["id"]]


def test_embedding_cache_is_versioned_and_old_vectors_are_not_reused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    item = _verified("Database optimizer", "Execution plan regression.", "Inspect row estimates.")
    calls = []

    def fake_embed(texts, model=None):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(ollama, "embed", fake_embed)
    experience.search_relevant_experiences("Database optimizer")
    updated = experience.update_experience(
        item["id"],
        takeaway="Compare actual and estimated row counts.",
        _controlled_run=True,
    )
    assert updated["version"] == item["version"] + 1

    experience.search_relevant_experiences("Database optimizer")
    with learning_connection() as connection:
        versions = connection.execute(
            "SELECT experience_version FROM learning_experience_embeddings "
            "WHERE experience_id=? ORDER BY experience_version",
            (item["id"],),
        ).fetchall()
    assert [row[0] for row in versions] == [item["version"], updated["version"]]
    assert len(calls) == 4
