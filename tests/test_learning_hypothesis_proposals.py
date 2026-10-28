import json

import pytest

from app import db, experience, ollama


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()


def _proposal():
    return {
        "hypotheses": [
            {
                "hypothesis": "The cache invalidation path retains a stale value after a write.",
                "falsification_criteria": "The hypothesis is falsified if repeated reads after a committed write always return the new value.",
                "test_plan": [
                    {
                        "test": "write then read regression",
                        "expected": "A read after commit returns the newly written value.",
                        "falsifies_hypothesis": "Ten isolated write/read cycles all return the latest value.",
                        "source_focus": "Official cache invalidation and transaction documentation.",
                    }
                ],
                "source_queries": ["official documentation cache invalidation after write"],
            }
        ]
    }


def test_hypothesis_proposals_are_saved_as_unverified_versioned_drafts(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ollama,
        "chat",
        lambda *_args, **kwargs: json.dumps(_proposal()),
    )

    result = experience.propose_hypotheses(
        "Cache consistency",
        "Reads sometimes appear to return a previous value after a write.",
        max_candidates=2,
    )

    assert len(result) == 1
    item = result[0]
    assert item["status"] == "identified"
    assert item["version"] == 1
    assert item["source_evidence"] == []
    assert item["test_results"] == []
    assert item["test_plan"][0]["test"] == "write then read regression"
    assert item["test_plan"][0]["source_queries"] == ["official documentation cache invalidation after write"]
    assert item["source_queries"] == ["official documentation cache invalidation after write"]
    assert "falsified if repeated reads" in item["falsification_criteria"]
    assert "not verified" in item["problem"]
    assert "verification_required" not in item


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"hypotheses": []}),
        json.dumps({"hypotheses": [{"hypothesis": "duplicate keys omitted"}]}),
        json.dumps({
            "hypotheses": [{
                "hypothesis": "A hypothesis",
                "falsification_criteria": "A falsifier",
                "test_plan": [{"test": "only partial schema"}],
                "source_queries": ["query"],
            }]
        }),
    ],
)
def test_invalid_hypothesis_output_fails_closed_without_creating_records(
    tmp_path, monkeypatch, raw
):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ollama, "chat", lambda *_args, **kwargs: raw)

    with pytest.raises(ValueError):
        experience.propose_hypotheses("Topic", "A reproducible problem.")

    with db.learning_connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM learning_experiences").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize(
    "topic,problem,max_candidates",
    [
        ("", "problem", 1),
        ("topic", "", 1),
        ("topic", "problem", 0),
        ("topic", "problem", 4),
        ("topic", "problem", True),
    ],
)
def test_hypothesis_request_bounds_are_enforced(topic, problem, max_candidates):
    with pytest.raises(ValueError):
        experience.propose_hypotheses(topic, problem, max_candidates=max_candidates)



def test_experience_hypotheses_require_and_link_retained_knowledge(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from app import learning

    job = learning.create_job("Retained source lesson")
    with db.learning_connection() as connection:
        knowledge_id = int(connection.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES (?, ?, ?, ?, 'retained_after_review', ?)""",
            ("Cache consistency", "Retained knowledge content.", "web_retrieved", 1, job["id"]),
        ).lastrowid)
    monkeypatch.setattr(ollama, "chat", lambda *_args, **kwargs: json.dumps(_proposal()))

    items = experience.propose_hypotheses_from_retained_knowledge(
        knowledge_id, "Investigate the cache invalidation experience.", max_candidates=1
    )

    assert len(items) == 1
    assert items[0]["originating_knowledge_id"] == knowledge_id
    assert items[0]["originating_knowledge_version"] == 1
    assert items[0]["status"] == "identified"
    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT originating_knowledge_id FROM learning_experiences WHERE id=?",
            (items[0]["id"],),
        ).fetchone()
    assert row["originating_knowledge_id"] == knowledge_id


def test_experience_hypotheses_do_not_start_from_unretained_knowledge(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from app import learning

    job = learning.create_job("Unretained source lesson")
    with db.learning_connection() as connection:
        knowledge_id = int(connection.execute(
            """INSERT INTO knowledge_items(topic, content, source_kind, version, status, job_id)
               VALUES (?, ?, ?, ?, 'unverified', ?)""",
            ("Cache consistency", "Unverified content.", "web_retrieved", 1, job["id"]),
        ).lastrowid)

    with pytest.raises(ValueError, match="requires retained_after_review"):
        experience.propose_hypotheses_from_retained_knowledge(
            knowledge_id, "Investigate this experience.", max_candidates=1
        )
