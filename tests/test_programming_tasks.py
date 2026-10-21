from app import db, ollama, programming_tasks
from app.task_worker import PermanentTaskError


class FakeTaskContext:
    task_id = 44

    def __init__(self):
        self.checkpoints = []

    def checkpoint(self, value):
        self.checkpoints.append(value)
        return value


def test_programming_handler_uses_programming_profile_and_never_claims_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    db.update_settings({
        "programming_ollama_model": "qwen3:14b",
        "programming_source_max_chars": 20000,
    })
    captured = {}

    def fake_chat(message, *, system_prompt=None, purpose="chat", **kwargs):
        captured.update(message=message, system_prompt=system_prompt, purpose=purpose)
        return "پیشنهاد کد؛ آزمون اجرا نشده است."

    monkeypatch.setattr(ollama, "chat", fake_chat)
    context = FakeTaskContext()
    result = programming_tasks.handle_programming_task(
        {"instruction": "این تابع را اصلاح کن", "context": "def example():\n    pass"},
        None,
        context,
    )

    assert captured["purpose"] == "programming"
    assert "این تابع را اصلاح کن" in captured["message"]
    assert "def example():" in captured["message"]
    assert "do not execute code" in captured["system_prompt"]
    assert result["execution_performed"] is False
    assert result["tests_executed"] is False
    assert [item["phase"] for item in context.checkpoints] == [
        "programming_assist_started",
        "programming_assist_completed",
    ]


def test_programming_payload_obeys_configured_source_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    db.update_settings({"programming_source_max_chars": 1000})

    try:
        programming_tasks.validate_payload({"instruction": "fix this", "context": "x" * 1001})
    except ValueError as exc:
        assert "configured programming source limit" in str(exc)
    else:
        raise AssertionError("Oversized programming context should be rejected")


def test_programming_handler_classifies_invalid_payload_as_permanent(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()

    with pytest.raises(PermanentTaskError, match="instruction must contain"):
        programming_tasks.handle_programming_task({"instruction": ""}, None, FakeTaskContext())
