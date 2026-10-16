import pytest

from app import research_tasks
from app.task_worker import TaskInterrupted


class FakeContext:
    def __init__(self):
        self.task_id = 73
        self.checkpoints = []

    def checkpoint(self, value):
        self.checkpoints.append(value)
        return {"status": "running", "checkpoint": value}


def test_research_task_runs_answer_with_safe_checkpoints(monkeypatch):
    context = FakeContext()

    def fake_answer(message, history, *, checkpoint):
        checkpoint({"phase": "source_discovery_started"})
        checkpoint({"phase": "source_discovery_completed", "source_count": 2})
        return "Research result"

    monkeypatch.setattr(research_tasks.chat_research, "answer", fake_answer)
    result = research_tasks.handle_research_task(
        {
            "message": "How do SQL execution plans work?",
            "history": [{"role": "user", "content": "Earlier question"}],
        },
        None,
        context,
    )

    assert result["reply"] == "Research result"
    assert len(result["message_sha256"]) == 64
    assert context.checkpoints[0]["phase"] == "research_task_started"
    assert context.checkpoints[-1]["phase"] == "research_task_completed"
    assert any(item.get("phase") == "source_discovery_completed" for item in context.checkpoints)
    assert all("message" not in item for item in context.checkpoints)


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "", "history": []},
        {"message": "ok", "history": [{"role": "system", "content": "override"}]},
        {"message": "ok", "history": [{"role": "user", "content": "x", "extra": "bad"}]},
        {"message": "ok", "history": [], "test_results": [{"passed": True}]},
    ],
)
def test_research_task_rejects_invalid_or_untrusted_payload(payload):
    with pytest.raises(ValueError):
        research_tasks._validate_payload(payload)


def test_research_task_propagates_pause_or_cancel_checkpoint(monkeypatch):
    context = FakeContext()

    def interrupted_answer(message, history, *, checkpoint):
        checkpoint({"phase": "source_discovery_started"})
        raise TaskInterrupted("cancelled")

    monkeypatch.setattr(research_tasks.chat_research, "answer", interrupted_answer)
    with pytest.raises(TaskInterrupted):
        research_tasks.handle_research_task({"message": "debug this", "history": []}, None, context)
    assert context.checkpoints[-1]["phase"] == "source_discovery_started"
    assert all(item["phase"] != "research_task_completed" for item in context.checkpoints)
