import pytest

from app import chat_feedback, chat_history, db


def _configure_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()


def test_response_feedback_is_persistent_changeable_and_not_knowledge(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)
    conversation = chat_history.create_conversation()
    chat_history.append_user_message(conversation["id"], "private user prompt")
    answer = chat_history.append_assistant_message(
        conversation["id"], "private assistant answer", task_id=501
    )

    first = chat_feedback.set_message_feedback(conversation["id"], answer["id"], -1)
    second = chat_feedback.set_message_feedback(conversation["id"], answer["id"], 1)

    assert first["rating"] == -1
    assert second["rating"] == 1
    messages = chat_history.list_messages(conversation["id"])
    assert messages[1]["feedback_rating"] == 1
    with db.settings_connection() as connection:
        rows = connection.execute(
            "SELECT message_id, rating FROM chat_message_feedback"
        ).fetchall()
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert [tuple(row) for row in rows] == [(answer["id"], 1)]
    assert "trusted_knowledge" not in tables
    assert "private user prompt" not in str(rows)
    assert "private assistant answer" not in str(rows)


@pytest.mark.parametrize("rating", [0, 2, -2, True, False, "1", None])
def test_response_feedback_rejects_invalid_ratings(tmp_path, monkeypatch, rating):
    _configure_databases(tmp_path, monkeypatch)
    conversation = chat_history.create_conversation()
    answer = chat_history.append_assistant_message(conversation["id"], "answer")

    with pytest.raises(ValueError):
        chat_feedback.set_message_feedback(conversation["id"], answer["id"], rating)


def test_response_feedback_only_accepts_assistant_messages(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)
    conversation = chat_history.create_conversation()
    user_message = chat_history.append_user_message(conversation["id"], "question")

    with pytest.raises(ValueError, match="assistant responses"):
        chat_feedback.set_message_feedback(conversation["id"], user_message["id"], 1)


def test_response_feedback_cannot_cross_conversation_boundaries(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)
    first = chat_history.create_conversation()
    answer = chat_history.append_assistant_message(first["id"], "answer")
    second = chat_history.create_conversation()

    with pytest.raises(KeyError):
        chat_feedback.set_message_feedback(second["id"], answer["id"], 1)
