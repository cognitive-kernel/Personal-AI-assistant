import json

from app import db, experience, experience_feedback, ollama
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



def test_embedding_cache_reads_all_batches_over_sqlite_parameter_limit(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with learning_connection() as connection:
        connection.executemany(
            """INSERT INTO learning_experiences (topic, problem, status, version)
               VALUES (?, ?, 'verified', 1)""",
            [(f"Cache item {index}", "Batch cache regression fixture") for index in range(405)],
        )
        ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM learning_experiences ORDER BY id"
            ).fetchall()
        ]
        connection.executemany(
            """INSERT INTO learning_experience_embeddings
                   (experience_id, experience_version, model, dimensions, embedding_json)
               VALUES (?, 1, 'test-embedding-model', 2, '[1.0, 0.0]')""",
            [(item_id,) for item_id in ids],
        )

    from app.experience_retrieval import _cached_embeddings

    cached = _cached_embeddings([(item_id, 1) for item_id in ids], "test-embedding-model")

    assert len(cached) == 405
    assert set(cached) == {(item_id, 1) for item_id in ids}
    assert all(vector == [1.0, 0.0] for vector in cached.values())


def test_explicit_negative_feedback_reduces_retrieval_rank_without_unverifying(
    tmp_path, monkeypatch
):
    _setup(tmp_path, monkeypatch)
    alternate = _verified(
        "Python JSON parser",
        "Parser rejects an empty payload.",
        "Check the schema and input boundaries.",
    )
    preferred = _verified(
        "Python JSON parser",
        "Parser rejects an empty payload.",
        "Validate input before parsing.",
    )

    def unavailable(*_args, **_kwargs):
        raise OllamaError("embedding model unavailable")

    monkeypatch.setattr(ollama, "embed", unavailable)
    before = experience.search_relevant_experiences("Python JSON parser")
    assert before[0]["id"] == preferred["id"]

    experience_feedback.set_feedback(preferred["id"], -1)
    after = experience.search_relevant_experiences("Python JSON parser")
    assert after[0]["id"] == alternate["id"]
    assert next(item for item in after if item["id"] == preferred["id"])[
        "retrieval_feedback"
    ]["rating"] == -1
    assert experience.get_experience(preferred["id"])["status"] == "verified"
