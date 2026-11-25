from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("startup_preflight", ROOT / "run.py")
assert SPEC is not None and SPEC.loader is not None
startup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(startup)


def configure_profiles(monkeypatch, models, installed):
    providers = {
        "chat": "ollama",
        "research_plan": "ollama",
        "learning": "ollama",
        "programming": "ollama",
        "embedding": "ollama",
        "factual_review": "ollama",
    }
    monkeypatch.setattr(startup, "_configured_llm_provider", lambda purpose: providers[purpose])
    monkeypatch.setattr(startup, "_configured_model_setting", lambda key, _env, _default: models[key])
    monkeypatch.setattr(startup, "_configured_learning_model", lambda: models["learning_ollama_model"])
    monkeypatch.setattr(
        startup,
        "_read_saved_setting",
        lambda key, default: models.get(key, default),
    )
    monkeypatch.setattr(startup, "_find_ollama", lambda: "ollama")
    output = "NAME ID SIZE\n" + "".join(f"{model} digest 1GB\n" for model in installed)
    monkeypatch.setattr(
        startup,
        "_ollama_list",
        lambda _executable: subprocess.CompletedProcess(["ollama", "list"], 0, output, ""),
    )
    monkeypatch.setattr(
        startup,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("Startup must never pull or install a model."),
    )


def test_startup_validates_every_purpose_model_and_keeps_optional_reviewer_optional(monkeypatch):
    models = {
        "chat_ollama_model": "chat-local",
        "research_plan_ollama_model": "research-local",
        "learning_ollama_model": "learning-local",
        "programming_ollama_model": "programming-local",
        "ollama_embedding_model": "embedding-local",
        "factual_review_ollama_model": "",
    }
    configure_profiles(monkeypatch, models, set(models[key] for key in (
        "chat_ollama_model",
        "research_plan_ollama_model",
        "learning_ollama_model",
        "programming_ollama_model",
        "ollama_embedding_model",
    )))
    assert startup._ensure_ollama() == []


def test_missing_programming_model_is_reported_without_automatic_pull(monkeypatch):
    models = {
        "chat_ollama_model": "chat-local",
        "research_plan_ollama_model": "research-local",
        "learning_ollama_model": "learning-local",
        "programming_ollama_model": "programming-not-installed",
        "ollama_embedding_model": "embedding-local",
        "factual_review_ollama_model": "",
    }
    installed = {
        "chat-local",
        "research-local",
        "learning-local",
        "embedding-local",
    }
    configure_profiles(monkeypatch, models, installed)
    warnings = startup._ensure_ollama()
    assert any("مدل برنامه‌نویسی" in warning for warning in warnings)
    assert not any("مدل گفتگو" in warning for warning in warnings)


def test_legacy_codex_context_alias_is_not_pulled_or_written_back(monkeypatch):
    models = {
        "chat_ollama_model": "qwen2.5:7b-codex-ctx2048",
        "research_plan_ollama_model": "research-local",
        "learning_ollama_model": "learning-local",
        "programming_ollama_model": "programming-local",
        "ollama_embedding_model": "embedding-local",
        "factual_review_ollama_model": "",
    }
    installed = {
        "qwen2.5:7b",
        "research-local",
        "learning-local",
        "programming-local",
        "embedding-local",
    }
    configure_profiles(monkeypatch, models, installed)
    warnings = startup._ensure_ollama()
    assert any("variant داخلی Codex" in warning for warning in warnings)
    assert not any("مدل‌های انتخاب‌شده نصب نیستند" in warning for warning in warnings)


def test_run_scoped_internal_context_variant_is_not_treated_as_registry_model(monkeypatch):
    models = {
        "chat_ollama_model": "qwen2.5:7b-codex-ctx2048-paa-123456-2-a1b2c3d4e5f6",
        "research_plan_ollama_model": "research-local",
        "learning_ollama_model": "learning-local",
        "programming_ollama_model": "programming-local",
        "ollama_embedding_model": "embedding-local",
        "factual_review_ollama_model": "",
    }
    installed = {
        "qwen2.5:7b",
        "research-local",
        "learning-local",
        "programming-local",
        "embedding-local",
    }
    configure_profiles(monkeypatch, models, installed)
    warnings = startup._ensure_ollama()

    assert any("variant داخلی Codex" in warning for warning in warnings)
    assert any("مدل پایهٔ qwen2.5:7b نصب است" in warning for warning in warnings)
    assert not any("مدل از UI" in warning and "qwen2.5:7b-codex" in warning for warning in warnings)
