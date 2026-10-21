import pytest

from app import db, model_runtime, ollama


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_legacy_single_model_settings_are_exposed_as_compatible_profiles():
    config = model_runtime.default_model_configuration({
        "ollama_model": "legacy-chat:7b",
        "ollama_embedding_model": "legacy-embed",
    })
    assert config["model_profiles"][0]["model"] == "legacy-chat:7b"
    assert config["task_model_assignments"]["simple_chat"] == "default-chat"
    assert config["task_model_assignments"]["embeddings"] == "default-embedding"


def test_each_task_uses_its_assigned_model_profile(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    config = model_runtime.default_model_configuration(db.get_settings())
    profiles = list(config["model_profiles"]) + [
        {"id": "learning-qwen3-8b", "name": "Learning", "provider": "ollama",
         "model": "qwen3:8b", "enabled": True},
        {"id": "programming-qwen3-14b", "name": "Programming", "provider": "ollama",
         "model": "qwen3:14b", "enabled": True},
    ]
    assignments = dict(config["task_model_assignments"])
    assignments["knowledge_learning"] = "learning-qwen3-8b"
    assignments["programming"] = "programming-qwen3-14b"
    db.update_settings({"model_profiles": profiles, "task_model_assignments": assignments})

    used = []
    monkeypatch.setattr(ollama, "chat", lambda *_args, **kwargs: used.append(kwargs["model"]) or "ok")
    assert model_runtime.chat("hello", task_type="simple_chat") == "ok"
    assert model_runtime.chat("lesson request", task_type="knowledge_learning") == "ok"
    assert model_runtime.chat("write a script", task_type="programming") == "ok"
    assert used == ["qwen2.5:7b", "qwen3:8b", "qwen3:14b"]


def test_model_configuration_rejects_unknown_provider_and_inactive_assignment():
    config = model_runtime.default_model_configuration({"ollama_model": "qwen2.5:7b"})
    profiles = [dict(item) for item in config["model_profiles"]]
    profiles[0]["provider"] = "private-custom-provider"
    with pytest.raises(ValueError, match="adapter"):
        model_runtime.validate_model_configuration(profiles, config["task_model_assignments"])

    profiles = [dict(item) for item in config["model_profiles"]]
    profiles[0]["enabled"] = False
    with pytest.raises(ValueError, match="پروفایل فعال"):
        model_runtime.validate_model_configuration(profiles, config["task_model_assignments"])


def test_task_assignment_must_cover_every_registered_task():
    config = model_runtime.default_model_configuration({"ollama_model": "qwen2.5:7b"})
    partial = dict(config["task_model_assignments"])
    partial.pop("programming")
    with pytest.raises(ValueError, match="همهٔ انواع وظیفه"):
        model_runtime.validate_model_configuration(config["model_profiles"], partial)
