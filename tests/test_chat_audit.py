import hashlib
import json
import sqlite3

import pytest

from app import chat_audit, chat_research, db, ollama


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_research_audit_stores_hashes_and_provenance_without_raw_content(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    question = "private question: database credential rotation"
    answer = "private answer: rotate the credential with this exact command"
    excerpt = "Official source excerpt with enough content to verify a technical claim safely."
    run_id = chat_audit.record_research_run(
        question=question,
        answer=answer,
        sources=[
            {"url": "https://docs.example.org/security", "title": "Security Guide", "content": excerpt},
            {"url": "http://localhost/private", "title": "Invalid local source", "content": "secret"},
        ],
        review_status="passed_model_assisted",
        review={
            "passed": True,
            "overall": "supported",
            "claims": [
                {"claim": "private claim text should not be persisted", "verdict": "supported", "evidence_urls": ["https://docs.example.org/security"]},
                {"claim": "another private claim", "verdict": "supported"},
            ],
            "reviewer": "local_ollama_model_assisted_not_independent",
        },
        memory={
            "verified_experience_guidance": [{"id": 7, "version": 3}],
            "failure_warnings": [{"id": 8, "version": 1}],
        },
        duration_ms=123,
    )

    with db.learning_connection() as connection:
        row = connection.execute("SELECT * FROM chat_research_runs WHERE id=?", (run_id,)).fetchone()
    assert row["query_sha256"] == hashlib.sha256(question.encode()).hexdigest()
    assert row["answer_sha256"] == hashlib.sha256(answer.encode()).hexdigest()
    assert row["route"] == "technical_research"
    assert row["source_count"] == 1
    assert row["review_status"] == "passed_model_assisted"
    assert row["duration_ms"] == 123

    sources = json.loads(row["sources_json"])
    review = json.loads(row["review_summary_json"])
    memory = json.loads(row["memory_refs_json"])
    assert sources == [{
        "url": "https://docs.example.org/security",
        "title": "Security Guide",
        "content_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
        "excerpt_chars": len(excerpt),
    }]
    assert review["verdict_counts"] == {"supported": 2}
    assert "private claim text" not in row["review_summary_json"]
    assert memory == [
        {"id": 7, "version": 3, "status": "verified", "kind": "verified_experience_guidance"},
        {"id": 8, "version": 1, "status": "warning", "kind": "failure_warnings"},
    ]

    serialized = json.dumps(dict(row), ensure_ascii=False)
    assert question not in serialized
    assert answer not in serialized
    assert excerpt not in serialized
    assert "secret" not in serialized


def test_research_audit_bounds_duration_and_rejects_unknown_status(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    run_id = chat_audit.record_research_run(
        question="query",
        answer="answer",
        sources=[],
        review_status="no_sources",
        duration_ms=-50,
    )
    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT duration_ms, source_count FROM chat_research_runs WHERE id=?", (run_id,)
        ).fetchone()
    assert row["duration_ms"] == 0
    assert row["source_count"] == 0

    with pytest.raises(ValueError, match="Unknown research review status"):
        chat_audit.record_research_run(
            question="query",
            answer="answer",
            sources=[],
            review_status="verified",
        )



def test_research_chat_persists_audit_without_raw_question_or_answer(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(chat_research.experience, "search_relevant_experiences", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_research.experience, "search_relevant_failures", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ollama, "chat", lambda *_args, **_kwargs: "The database answer is model-generated.")

    question = "How does a database query plan work?"
    answer = chat_research.answer(question)
    assert "not independently reviewed" in answer

    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT query_sha256, answer_sha256, review_status FROM chat_research_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["query_sha256"] == hashlib.sha256(question.encode()).hexdigest()
    assert row["answer_sha256"] == hashlib.sha256(answer.encode()).hexdigest()
    assert row["review_status"] == "no_sources"
