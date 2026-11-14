import pytest

from app.learning_model_adapter import (
    LearningModelContractError,
    request_lesson_json,
)


@pytest.mark.parametrize("provider_purpose", ["ai_learning", "learning", "topic_learning"])
def test_lesson_request_preserves_selected_method_provider_and_output_budget(provider_purpose):
    captured = {}

    def fake_request(path, payload, *, timeout, purpose):
        captured.update(path=path, payload=payload, timeout=timeout, purpose=purpose)
        return {"message": {"content": '{"lesson":"ok"}'}}

    result = request_lesson_json(
        fake_request,
        provider_purpose=provider_purpose,
        model=f"{provider_purpose}-model",
        system_prompt="Teach the topic.",
        user_prompt='{"topic":"SQL Server"}',
        context_tokens=8192,
        cpu_threads=6,
        timeout_seconds=180,
        max_output_tokens=2500,
    )

    assert result["message"]["content"] == '{"lesson":"ok"}'
    assert captured["path"] == "/api/chat"
    assert captured["purpose"] == provider_purpose
    assert captured["timeout"] == 180
    assert captured["payload"]["model"] == f"{provider_purpose}-model"
    assert captured["payload"]["options"] == {
        "num_ctx": 8192,
        "num_thread": 6,
        "num_predict": 2500,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_purpose": "research_plan"},
        {"context_tokens": True},
        {"cpu_threads": -1},
        {"timeout_seconds": 0},
        {"max_output_tokens": 8193},
        {"model": ""},
        {"user_prompt": " "},
    ],
)
def test_lesson_request_rejects_invalid_contract_without_dispatch(overrides):
    captured = []

    def fake_request(*args, **kwargs):
        captured.append((args, kwargs))
        return {}

    values = {
        "provider_purpose": "ai_learning",
        "model": "model",
        "system_prompt": "system",
        "user_prompt": "prompt",
        "context_tokens": 4096,
        "cpu_threads": 0,
        "timeout_seconds": 60,
        "max_output_tokens": 3900,
    }
    values.update(overrides)

    with pytest.raises(LearningModelContractError):
        request_lesson_json(fake_request, **values)
    assert captured == []
