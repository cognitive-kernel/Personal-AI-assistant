import json

import pytest

from app import chat_research, ollama, research_planner


def _plan():
    return [
        {
            "question": "Which official documentation defines the feature?",
            "source_focus": "Official vendor documentation and versioned reference pages",
            "acceptance_criteria": "A primary source URL and the relevant version scope are identified.",
        },
        {
            "question": "What behavior and limitations are documented?",
            "source_focus": "Official API/reference documentation and release notes",
            "acceptance_criteria": "The answer distinguishes supported behavior from limitations and uncertainty.",
        },
    ]


class FakeContext:
    def __init__(self, checkpoint=None):
        self.task_id = 501
        self.worker_id = "research-plan-test"
        self.checkpoint_data = checkpoint
        self.saved = []

    def checkpoint(self, value):
        self.checkpoint_data = value
        self.saved.append(value)
        return {"status": "running", "checkpoint": value}


def test_plan_response_requires_bounded_unique_structured_steps():
    plan = _plan()
    parsed = research_planner._parse_plan_response(
        json.dumps({"steps": plan}), max_steps=3
    )
    assert parsed == plan

    with pytest.raises(ValueError, match="duplicate"):
        research_planner.validate_plan(
            {"steps": [plan[0], {**plan[0]}]}, max_steps=3
        )
    with pytest.raises(ValueError, match="step count"):
        research_planner.validate_plan({"steps": [plan[0]]}, max_steps=3)


def test_build_plan_uses_bounded_research_plan_inference(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        ollama,
        "chat",
        lambda message, **kwargs: captured.update(
            message=json.loads(message), kwargs=kwargs
        ) or json.dumps({"steps": _plan()}),
    )

    result = research_planner.build_plan("Compare two SQL Server features", max_steps=3)

    assert len(result) == 2
    assert captured["kwargs"]["purpose"] == "research_plan"
    assert captured["message"]["max_steps"] == 3
    assert "Do not execute code" in captured["message"]["instruction"]


def test_plan_handler_runs_steps_and_persists_progress(monkeypatch):
    plan = _plan()
    monkeypatch.setattr(research_planner, "build_plan", lambda *_args, **_kwargs: plan)
    answers = []
    monkeypatch.setattr(
        chat_research,
        "answer",
        lambda message, history, checkpoint=None: answers.append(message) or f"Answer {len(answers)}",
    )
    context = FakeContext()

    result = research_planner.handle_research_plan_task(
        {"goal": "Compare two SQL Server features", "max_steps": 3},
        None,
        context,
    )

    assert result["status"] == "completed"
    assert result["completed_steps"] == result["total_steps"] == 2
    assert len(answers) == 2
    assert "Source focus:" in answers[0]
    assert context.checkpoint_data["next_step_index"] == 2
    assert len(context.checkpoint_data["completed_results"]) == 2


def test_plan_handler_resumes_from_valid_checkpoint_without_repeating_completed_steps(monkeypatch):
    plan = _plan()
    goal = "Compare two SQL Server features"
    import hashlib

    checkpoint = {
        "goal_sha256": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
        "plan": plan,
        "completed_results": [
            {"step_index": 0, "question": plan[0]["question"], "answer": "Previously completed answer"}
        ],
        "next_step_index": 1,
    }
    monkeypatch.setattr(
        research_planner,
        "build_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should resume saved plan")),
    )
    calls = []
    monkeypatch.setattr(chat_research, "answer", lambda message, history, checkpoint=None: calls.append(message) or "Second answer")
    context = FakeContext(checkpoint)

    result = research_planner.handle_research_plan_task(
        {"goal": goal, "max_steps": 3},
        checkpoint,
        context,
    )

    assert len(calls) == 1
    assert result["completed_steps"] == 2
    assert result["results"][0]["answer"] == "Previously completed answer"
