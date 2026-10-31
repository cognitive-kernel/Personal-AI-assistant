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


def test_review_summary_retains_bounded_model_and_prompt_provenance():
    prompt_digest = "a" * 64
    model_digest = "b" * 64
    summary = chat_audit._review_summary({
        "passed": True,
        "overall": "supported",
        "claims": [{"verdict": "supported"}],
        "prompt_version": "claim-review-v2",
        "prompt_sha256": prompt_digest,
        "model_id": "qwen2.5:7b",
        "model_digest": model_digest,
        "model_digest_status": "verified_local_manifest",
        "provider_usage": {
            "prompt_eval_count": 123,
            "eval_count": 45,
            "total_duration": 678,
            "eval_duration": 600,
            "private_prompt": "must not persist",
            "negative_counter": -1,
            "boolean_counter": True,
            "oversized_counter": 10**30,
        },
    })
    assert summary["prompt_version"] == "claim-review-v2"
    assert summary["prompt_sha256"] == prompt_digest
    assert summary["model_id"] == "qwen2.5:7b"
    assert summary["model_digest"] == model_digest
    assert summary["model_digest_status"] == "verified_local_manifest"
    assert summary["provider_usage"] == {
        "prompt_eval_count": 123,
        "eval_count": 45,
        "total_duration": 678,
        "eval_duration": 600,
    }
    assert "private_prompt" not in json.dumps(summary)


def test_review_summary_rejects_malformed_provenance_identifiers():
    summary = chat_audit._review_summary({
        "passed": False,
        "claims": [],
        "prompt_version": "x" * 1000,
        "prompt_sha256": "not-a-digest",
        "model_digest": "z" * 64,
        "model_id": "m" * 300,
        "model_digest_status": "s" * 1000,
        "provider_usage": {"eval_count": "12", "total_duration": True},
    })
    assert "prompt_sha256" not in summary
    assert "model_digest" not in summary
    assert len(summary["prompt_version"]) == 80
    assert len(summary["model_id"]) == 200
    assert len(summary["model_digest_status"]) == 80
    assert "provider_usage" not in summary


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
    monkeypatch.setattr(
        chat_research.web_sources,
        "discover_sources",
        lambda *_args, **_kwargs: chat_research.web_sources.SourceDocumentList(
            search_provider_diagnostics=[{
                "provider": "DuckDuckGo",
                "attempts": [{
                    "variant": "primary", "endpoint_variant": "html",
                    "error": "no_parseable_results", "private_url": "https://private.invalid/path",
                }],
                "parsed_result_count": 0, "accepted_result_count": 0,
                "unique_result_count": 0, "duplicate_result_count": 0,
            }],
            fetch_diagnostics={
                "candidate_count": 2, "attempted_count": 2, "fetched_count": 0,
                "failure_counts": {"SourceFetchError": 2},
                "private_query": "must not be persisted",
            },
        ),
    )
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
    with db.learning_connection() as connection:
        summary_row = connection.execute(
            "SELECT review_summary_json FROM chat_research_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    summary = json.loads(summary_row["review_summary_json"])
    diagnostics = summary["source_discovery"]
    assert diagnostics["fetch"] == {
        "candidate_count": 2, "attempted_count": 2, "fetched_count": 0,
        "failure_counts": {"SourceFetchError": 2},
    }
    assert diagnostics["providers"][0]["attempts"][0]["error"] == "no_parseable_results"
    assert "private.invalid" not in json.dumps(diagnostics)
    assert "must not be persisted" not in json.dumps(diagnostics)


def test_review_summary_preserves_conflict_url_pairs_without_raw_conflict_text():
    summary = chat_audit._review_summary({
        "passed": False,
        "overall": "insufficient_evidence",
        "claims": [],
        "source_conflicts": [{
            "topic": "private topic text",
            "source_a_url": "https://docs.example.org/a",
            "source_b_url": "https://docs.example.org/b",
            "description": "private explanatory text must not be persisted",
        }],
        "error": "source_conflicts_require_human_review",
    })
    assert summary["source_conflict_count"] == 1
    assert summary["source_conflict_pairs"] == [{
        "source_a_url": "https://docs.example.org/a",
        "source_b_url": "https://docs.example.org/b",
    }]
    assert "private topic text" not in json.dumps(summary)
    assert "private explanatory text" not in json.dumps(summary)

def test_research_chat_retains_provider_diagnostics_when_search_fails(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(chat_research.experience, "search_relevant_experiences", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_research.experience, "search_relevant_failures", lambda *_args, **_kwargs: [])
    failure = chat_research.web_sources.SourceFetchError("provider failure with private response details")
    failure.provider_diagnostics = [{
        "provider": "Google",
        "attempts": [{
            "variant": "primary", "endpoint_variant": "standard",
            "error": "challenge_or_consent_page",
            "private_url": "https://private.invalid/search?q=secret",
        }],
        "parsed_result_count": 0, "accepted_result_count": 0,
        "unique_result_count": 0, "duplicate_result_count": 0,
    }]

    def fail_discovery(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(chat_research.web_sources, "discover_sources", fail_discovery)
    monkeypatch.setattr(ollama, "chat", lambda *_args, **_kwargs: "A cautious answer without external sources.")

    answer = chat_research.answer("How does a database query plan work?")
    assert "No usable external source was found" in answer

    with db.learning_connection() as connection:
        row = connection.execute(
            "SELECT review_summary_json FROM chat_research_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    diagnostics = json.loads(row["review_summary_json"])["source_discovery"]
    assert diagnostics["providers"][0]["provider"] == "Google"
    assert diagnostics["providers"][0]["attempts"][0]["error"] == "challenge_or_consent_page"
    assert "private.invalid" not in json.dumps(diagnostics)
    assert "secret" not in json.dumps(diagnostics)
    assert "private response details" not in json.dumps(diagnostics)
