from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.autonomous_engineering_contracts import (
    CODEX_CONTEXT_VARIANT_PATTERN,
    CORRELATION_ID_PATTERN,
    HTTP_ERROR_CODES,
    MODEL_PATTERN,
    RETRYABLE_HTTP_STATUSES,
    CancelRequest,
    EngineeringSettings,
    ManualTaskRequest,
    ModelPullRequest,
)


def test_engineering_settings_contract_preserves_strict_types_and_default_mode():
    settings = EngineeringSettings(model="qwen2.5-coder:14b", interval_hours=6)
    assert settings.execution_mode == "continuous"

    with pytest.raises(ValidationError):
        EngineeringSettings(model=123, interval_hours=6)
    with pytest.raises(ValidationError):
        EngineeringSettings(model="qwen2.5-coder:14b", interval_hours=True)


def test_manual_task_contract_keeps_empty_default_and_length_limit():
    assert ManualTaskRequest().task == ""
    assert ManualTaskRequest(task="inspect tests").task == "inspect tests"

    with pytest.raises(ValidationError):
        ManualTaskRequest(task=None)
    with pytest.raises(ValidationError):
        ManualTaskRequest(task="x" * 2001)


def test_model_pull_and_cancel_contracts_reject_invalid_or_coerced_values():
    assert ModelPullRequest(model="llama3.1:8b").model == "llama3.1:8b"
    assert CancelRequest(run_id=42).run_id == 42

    for payload in ({"model": ""}, {"model": "x" * 121}, {"model": 42}):
        with pytest.raises(ValidationError):
            ModelPullRequest(**payload)
    for payload in ({"run_id": 0}, {"run_id": -1}, {"run_id": True}, {"run_id": "42"}):
        with pytest.raises(ValidationError):
            CancelRequest(**payload)


def test_shared_protocol_patterns_and_retry_contract_are_stable():
    assert CORRELATION_ID_PATTERN.fullmatch("a" * 32)
    assert not CORRELATION_ID_PATTERN.fullmatch("A" * 32)
    assert MODEL_PATTERN.fullmatch("qwen2.5-coder:14b")
    assert not MODEL_PATTERN.fullmatch("invalid model")
    assert CODEX_CONTEXT_VARIANT_PATTERN.search("qwen2.5-coder-codex-ctx65536")
    assert 429 in RETRYABLE_HTTP_STATUSES
    assert 400 not in RETRYABLE_HTTP_STATUSES
    assert HTTP_ERROR_CODES[422] == "AUTONOMOUS_ENGINEERING_VALIDATION_FAILED"
