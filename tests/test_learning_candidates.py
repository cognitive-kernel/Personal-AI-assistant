from __future__ import annotations

from app import db, learning, learning_candidates


def _initialize_learning_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def _capture_candidate(*, conversation_id: int, query: str, answer: str):
    return learning_candidates.capture_chat_answer(
        conversation_id=conversation_id,
        message_id=conversation_id + 1000,
        user_message_id=conversation_id + 2000,
        task_id=None,
        user_query=query,
        answer=answer,
        created_at="2026-11-11T00:00:00+00:00",
    )


def test_relevant_chat_candidates_are_retrieved_as_unverified_local_observations(tmp_path, monkeypatch):
    _initialize_learning_database(tmp_path, monkeypatch)
    sql_answer = ("SQL Server backup and restore behavior, recovery models, and transaction log handling. " * 8)
    python_answer = ("Python decorators wrap callables and preserve metadata with functools.wraps. " * 8)
    _capture_candidate(
        conversation_id=1,
        query="How do SQL Server 2022 backups and restores work?",
        answer=sql_answer,
    )
    _capture_candidate(
        conversation_id=2,
        query="Explain Python decorators",
        answer=python_answer,
    )

    result = learning_candidates.relevant_chat_candidates("SQL Server 2022")

    assert result["matched_count"] == 1
    assert result["included_count"] == 1
    assert result["omitted_count"] == 0
    item = result["items"][0]
    assert item["content"] == sql_answer.strip()
    assert item["status"] == "unverified"
    assert item["provenance"]["storage"] == "local_only"
    assert item["source_kind"] == "chat_answer"
    assert item["content_truncated"] is False
    assert learning_candidates.list_chat_candidates(status="unverified", limit=10)[0]["status"] == "unverified"


def test_candidate_prompt_context_reports_omitted_items_without_promoting_them(tmp_path, monkeypatch):
    _initialize_learning_database(tmp_path, monkeypatch)
    first = ("SQL Server indexing and execution plan behavior. " * 30)
    second = ("SQL Server transaction isolation and locking behavior. " * 30)
    _capture_candidate(conversation_id=10, query="SQL Server indexes", answer=first)
    _capture_candidate(conversation_id=11, query="SQL Server transactions", answer=second)

    result = learning_candidates.relevant_chat_candidates(
        "SQL Server", max_candidates=1, max_chars=1000
    )

    assert result["matched_count"] == 2
    assert result["included_count"] == 1
    assert result["omitted_count"] == 1
    assert result["items"][0]["content_truncated"] is True
    assert result["items"][0]["status"] == "unverified"
    assert all(item["status"] == "unverified" for item in learning_candidates.list_chat_candidates())


def test_lesson_generation_candidate_retrieval_is_fail_open_and_configurable(monkeypatch):
    captured = {}

    def retrieve(topic, *, max_chars):
        captured["topic"] = topic
        captured["max_chars"] = max_chars
        return {"items": [{"candidate_id": 7, "status": "unverified"}], "matched_count": 1}

    monkeypatch.setattr(learning_candidates, "relevant_chat_candidates", retrieve)
    result = learning._prior_assistant_observations(
        "SQL Server", {"learning_source_max_chars": 50000}
    )

    assert captured == {"topic": "SQL Server", "max_chars": 50000}
    assert result["status"] == "available"
    assert result["items"][0]["status"] == "unverified"

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("candidate database unavailable")

    monkeypatch.setattr(learning_candidates, "relevant_chat_candidates", unavailable)
    fallback = learning._prior_assistant_observations("SQL Server", {})
    assert fallback["status"] == "unavailable"
    assert fallback["items"] == []
    assert fallback["error_type"] == "RuntimeError"



def test_chat_observation_pages_cover_content_matches_and_long_answers_without_loss(tmp_path, monkeypatch):
    _initialize_learning_database(tmp_path, monkeypatch)
    answers = [
        ("SQL Server transaction log recovery and point-in-time restore. " * 35).strip(),
        ("SQL Server indexing and query execution plans. " * 18).strip(),
        ("SQL Server isolation levels and locking. " * 18).strip(),
    ]
    for index, answer in enumerate(answers, start=20):
        _capture_candidate(
            conversation_id=index,
            query="Please explain this technical issue",
            answer=answer,
        )

    cursor = None
    chunks = {}
    pages = 0
    while True:
        page = learning_candidates.relevant_chat_candidates(
            "SQL Server", max_candidates=1, max_chars=1000, cursor=cursor
        )
        pages += 1
        assert page["items"]
        item = page["items"][0]
        chunks.setdefault(item["candidate_id"], []).append(item["content"])
        assert item["status"] == "unverified"
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        assert isinstance(page["next_cursor"], dict)
        cursor = page["next_cursor"]
        assert pages < 10

    assert len(chunks) == 3
    reconstructed = {
        candidate_id: "".join(parts)
        for candidate_id, parts in chunks.items()
    }
    expected = {
        candidate["id"]: candidate["content"]
        for candidate in learning_candidates.list_chat_candidates(status="unverified", limit=20)
    }
    assert reconstructed == {
        candidate_id: expected[candidate_id]
        for candidate_id in reconstructed
    }
    assert set(reconstructed) == set(expected)
