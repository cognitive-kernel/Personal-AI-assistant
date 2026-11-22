from __future__ import annotations

from scripts import autonomous_engineering_codex_config as config


def test_local_codex_task_budget_allows_slow_cpu_inference():
    assert config.CODEX_TASK_TIMEOUT_SECONDS == 1800


def _overrides(arguments: list[str]) -> set[str]:
    return {
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == "-c"
    }


def test_task_stream_uses_long_first_token_window_without_nested_provider_retries(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "65536")
    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "mxc")
    monkeypatch.delenv("CODEX_MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(config.tempfile, "gettempdir", lambda: str(tmp_path))

    arguments = config.codex_arguments(
        "gpt-oss:20b-codex-ctx65536",
        str(tmp_path / "codex-report-1.md"),
    )
    overrides = _overrides(arguments)

    assert "model_providers.ollama-local.stream_idle_timeout_ms=900000" in overrides
    assert "model_providers.ollama-local.stream_max_retries=0" in overrides
    assert "model_providers.ollama-local.request_max_retries=0" in overrides
    assert not any("stream_idle_timeout_ms=120000" in value for value in overrides)


def test_sandbox_smoke_keeps_its_strict_short_stream_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTONOMOUS_ENGINEERING_CONTEXT_LENGTH", "65536")
    monkeypatch.setenv("CODEX_WINDOWS_SANDBOX_BACKEND", "mxc")
    monkeypatch.delenv("CODEX_MODEL_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(config.tempfile, "gettempdir", lambda: str(tmp_path))

    arguments = config.codex_arguments(
        "gpt-oss:20b-codex-ctx65536",
        str(tmp_path / "codex-sandbox-smoke-report.md"),
    )
    overrides = _overrides(arguments)

    assert "model_providers.ollama-local.stream_idle_timeout_ms=120000" in overrides
    assert "model_providers.ollama-local.stream_max_retries=0" in overrides
    assert "model_providers.ollama-local.request_max_retries=0" in overrides
    assert not any("stream_idle_timeout_ms=900000" in value for value in overrides)
