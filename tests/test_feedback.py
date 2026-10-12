import json

from app import db, feedback, ollama


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    feedback.init_feedback_table()


def test_feedback_is_persisted_and_retrieved_by_relevance(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    good = feedback.record_feedback(
        "Explain Python JSON errors",
        "A long answer about JSON parsing.",
        "unhelpful",
        "Start with the cause, then provide a minimal reproducible fix.",
    )
    feedback.record_feedback(
        "Write a SQL query",
        "A concise SELECT query.",
        "helpful",
        "Prefer runnable examples and explain assumptions.",
    )

    matches = feedback.search_relevant_feedback("Python JSON parsing error", limit=3)
    assert matches
    assert matches[0]["id"] == good["id"]
    context = feedback.feedback_prompt_context("Python JSON parsing error")
    assert "warning only, not factual evidence" in context
    assert "minimal reproducible fix" in context


def test_chat_injects_feedback_as_guidance_not_evidence(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    feedback.record_feedback(
        "Python JSON parsing",
        "The previous answer was too abstract.",
        "unhelpful",
        "Show a minimal runnable example first.",
    )
    captured = {}

    def fake_request(_path, payload, **_kwargs):
        captured["payload"] = payload
        return {"message": {"content": "Try json.loads(text) and handle JSONDecodeError."}}

    monkeypatch.setattr(ollama, "_request", fake_request)
    result = ollama.chat("How do I fix Python JSON parsing?")
    assert result.startswith("Try json.loads")
    messages = captured["payload"]["messages"]
    assert messages[0]["role"] == "system"
    assert "not factual evidence" in messages[0]["content"]
    assert "minimal runnable example" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "How do I fix Python JSON parsing?"}


def test_feedback_validation_rejects_invalid_rating(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    try:
        feedback.record_feedback("question", "answer", "maybe")
    except ValueError as exc:
        assert "rating" in str(exc)
    else:
        raise AssertionError("Invalid feedback rating should be rejected.")
