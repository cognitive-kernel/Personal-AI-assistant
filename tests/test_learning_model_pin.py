import importlib.util
import subprocess
from pathlib import Path

import pytest

from app import db, learning


def test_existing_builtin_learning_default_migrates_but_custom_choice_is_preserved(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("LEARNING_OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))

    db.init_databases()
    assert db.get_settings()["learning_ollama_model"] == "gpt-oss:20b"

    db.update_settings({"learning_ollama_model": "qwen3:8b"})
    db.init_databases()
    assert db.get_settings()["learning_ollama_model"] == "gpt-oss:20b"

    db.update_settings({"learning_ollama_model": "custom-learning:latest"})
    db.init_databases()
    assert db.get_settings()["learning_ollama_model"] == "custom-learning:latest"


def test_learning_model_selection_never_switches_to_a_cheaper_installed_model(monkeypatch):
    monkeypatch.setattr(
        learning.ollama,
        "list_models",
        lambda: [
            {"name": "gpt-oss:20b"},
            {"name": "qwen3:8b"},
        ],
    )
    settings = {
        "learning_model_cost_profiles": {
            "gpt-oss:20b": {
                "input_cost_microusd_per_1k_tokens": 1000,
                "output_cost_microusd_per_1k_tokens": 1000,
                "quality_score": 0.95,
            },
            "qwen3:8b": {
                "input_cost_microusd_per_1k_tokens": 1,
                "output_cost_microusd_per_1k_tokens": 1,
                "quality_score": 0.95,
            },
        }
    }

    selected, provenance = learning._select_learning_generation_model(
        settings, "lesson prompt", "gpt-oss:20b"
    )

    assert selected == "gpt-oss:20b"
    assert provenance["selected_model"] == "gpt-oss:20b"
    assert provenance["candidates_considered"] == 1


def test_learning_model_selection_fails_closed_instead_of_substituting_another_model(
    monkeypatch,
):
    monkeypatch.setattr(
        learning.ollama,
        "list_models",
        lambda: [{"name": "qwen3:8b"}],
    )
    settings = {
        "learning_model_minimum_quality_score": 0.8,
        "learning_model_cost_profiles": {
            "qwen3:8b": {
                "input_cost_microusd_per_1k_tokens": 1,
                "output_cost_microusd_per_1k_tokens": 1,
                "quality_score": 0.99,
            }
        },
    }

    with pytest.raises(learning.LearningError, match="not installed in Ollama"):
        learning._select_learning_generation_model(
            settings, "lesson prompt", "gpt-oss:20b"
        )


def _load_bootstrap():
    path = Path(__file__).resolve().parents[1] / "run.py"
    spec = importlib.util.spec_from_file_location("learning_model_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_startup_installs_and_verifies_learning_model_separately(monkeypatch):
    bootstrap = _load_bootstrap()
    monkeypatch.setattr(bootstrap, "_find_ollama", lambda: "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("LEARNING_OLLAMA_MODEL", raising=False)
    list_outputs = iter([
        "NAME ID SIZE\nqwen2.5:7b abc 4GB\n",
        "NAME ID SIZE\nqwen2.5:7b abc 4GB\ngpt-oss:20b def 14GB\n",
    ])
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, next(list_outputs), ""
        ),
    )
    pull_commands = []

    def fake_run(command, **kwargs):
        pull_commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)

    bootstrap._ensure_ollama()

    assert pull_commands == [["ollama", "pull", "gpt-oss:20b"]]


def test_startup_respects_explicit_learning_model_override(tmp_path, monkeypatch):
    bootstrap = _load_bootstrap()
    monkeypatch.setattr(bootstrap, "_find_ollama", lambda: "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("LEARNING_OLLAMA_MODEL", "custom-learning:latest")
    monkeypatch.setenv("SETTINGS_DB_PATH", str(tmp_path / "missing-settings.db"))
    list_outputs = iter([
        "NAME ID SIZE\nqwen2.5:7b abc 4GB\n",
        "NAME ID SIZE\nqwen2.5:7b abc 4GB\ncustom-learning:latest def 14GB\n",
    ])
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, next(list_outputs), ""
        ),
    )
    pull_commands = []

    def fake_run(command, **kwargs):
        pull_commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)

    bootstrap._ensure_ollama()

    assert pull_commands == [["ollama", "pull", "custom-learning:latest"]]


def test_startup_fails_when_learning_model_pull_fails(monkeypatch):
    bootstrap = _load_bootstrap()
    monkeypatch.setattr(bootstrap, "_find_ollama", lambda: "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("LEARNING_OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, "NAME ID SIZE\nqwen2.5:7b abc 4GB\n", ""
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "pull failed"),
    )

    with pytest.raises(RuntimeError, match="مدل یادگیری"):
        bootstrap._ensure_ollama()


def test_learning_model_selection_uses_configured_compatible_provider_without_ollama(
    monkeypatch,
):
    from app import learning_cost_manager

    seen = {}
    monkeypatch.setattr(
        learning.ollama, "list_models",
        lambda: (_ for _ in ()).throw(AssertionError("Ollama discovery must not run")),
    )

    def select(_settings, candidates, **_kwargs):
        seen["candidates"] = candidates
        return {
            "selected_model": candidates[0],
            "selection": {
                "estimated_cost_microusd": None,
                "estimated_energy_wh": None,
                "quality_score": None,
                "provider": "openai_compatible",
            },
            "status": "selected",
        }

    monkeypatch.setattr(learning_cost_manager, "select_learning_model", select)
    selected, provenance = learning._select_learning_generation_model(
        {
            "learning_llm_provider": "openai_compatible",
            "learning_model_cost_profiles": {
                "remote-model": {
                    "provider": "openai_compatible",
                    "input_cost_microusd_per_1k_tokens": 0,
                    "output_cost_microusd_per_1k_tokens": 0,
                    "quality_score": 0.9,
                }
            },
        },
        "lesson prompt",
        "remote-model",
    )

    assert selected == "remote-model"
    assert seen["candidates"] == ["remote-model"]
    assert provenance["selected_model"] == "remote-model"


def test_startup_skips_ollama_when_all_llm_and_embedding_profiles_are_external(monkeypatch, capsys):
    bootstrap = _load_bootstrap()
    monkeypatch.setattr(
        bootstrap, "_configured_llm_provider",
        lambda _purpose: "openai_compatible",
    )
    monkeypatch.setattr(bootstrap, "_read_saved_setting", lambda _key, default: default)
    monkeypatch.setattr(
        bootstrap, "_find_ollama",
        lambda: (_ for _ in ()).throw(AssertionError("Ollama must not be installed or started")),
    )

    bootstrap._ensure_ollama()

    assert "Ollama" in capsys.readouterr().out
