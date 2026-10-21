from app import chat_history, db


def _configure_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    monkeypatch.setattr(db.defaults, "cloud_resources_db_path", str(tmp_path / "cloud_resources.db"))
    db.init_databases()


def test_conversations_and_messages_persist_independently(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)

    first = chat_history.create_conversation()
    chat_history.append_user_message(first["id"], "سلام")
    chat_history.append_assistant_message(first["id"], "سلام، چطور می‌توانم کمک کنم؟", task_id=101)

    second = chat_history.create_conversation()
    chat_history.append_user_message(second["id"], "یک گفتگوی جدا")

    # A new conversation must not replace or hide the previous conversation.
    listed = {item["id"]: item for item in chat_history.list_conversations()}
    assert first["id"] in listed and second["id"] in listed
    assert listed[first["id"]]["title"] == "سلام"
    assert listed[first["id"]]["message_count"] == 2
    assert listed[second["id"]]["message_count"] == 1

    messages = chat_history.list_messages(first["id"])
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "سلام"),
        ("assistant", "سلام، چطور می‌توانم کمک کنم؟"),
    ]
    assert chat_history.get_recent_history(first["id"]) == [
        {"role": "user", "content": "سلام"},
        {"role": "assistant", "content": "سلام، چطور می‌توانم کمک کنم؟"},
    ]


def test_delete_conversation_removes_only_selected_conversation(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)
    first = chat_history.create_conversation()
    chat_history.append_user_message(first["id"], "این گفتگو حذف شود")
    second = chat_history.create_conversation()
    chat_history.append_user_message(second["id"], "این گفتگو بماند")

    assert chat_history.delete_conversation(first["id"]) is True
    assert chat_history.get_conversation(first["id"]) is None
    assert [item["id"] for item in chat_history.list_conversations()] == [second["id"]]
    assert chat_history.list_messages(second["id"])[0]["content"] == "این گفتگو بماند"
    assert chat_history.delete_conversation(first["id"]) is False


def test_assistant_task_result_is_idempotent(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)
    conversation = chat_history.create_conversation()

    first = chat_history.append_assistant_message(conversation["id"], "جواب", task_id=77)
    second = chat_history.append_assistant_message(conversation["id"], "جواب تکراری", task_id=77)

    assert first["id"] == second["id"]
    assert len(chat_history.list_messages(conversation["id"])) == 1


def test_unknown_conversation_is_not_silently_created(tmp_path, monkeypatch):
    _configure_databases(tmp_path, monkeypatch)

    try:
        chat_history.list_messages(999)
    except KeyError as exc:
        assert exc.args == (999,)
    else:
        raise AssertionError("Unknown conversation must return a not-found signal")


def test_research_worker_persists_assistant_reply(tmp_path, monkeypatch):
    from app import chat_research, research_tasks

    _configure_databases(tmp_path, monkeypatch)
    conversation = chat_history.create_conversation()
    chat_history.append_user_message(conversation["id"], "سلام")

    class Context:
        task_id = 202

        def checkpoint(self, value):
            return {"checkpoint": value, "status": "running"}

    monkeypatch.setattr(chat_research, "answer", lambda message, history, checkpoint=None: "پاسخ پایدار")
    result = research_tasks.handle_research_task(
        {"message": "سلام", "history": [], "conversation_id": conversation["id"]},
        None,
        Context(),
    )
    assert result["reply"] == "پاسخ پایدار"
    messages = chat_history.list_messages(conversation["id"])
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "سلام"),
        ("assistant", "پاسخ پایدار"),
    ]
    assert messages[1]["task_id"] == 202
