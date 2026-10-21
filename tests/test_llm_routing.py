import pytest

from app import chat_research, db, llm


PROFILES = [
    {"id": "chat-small", "label": "گفتگوی سبک", "provider": "ollama", "model": "qwen2.5:7b", "enabled": True},
    {"id": "learning-medium", "label": "یادگیری", "provider": "ollama", "model": "qwen3:8b", "enabled": True},
    {"id": "coding-large", "label": "برنامه‌نویسی", "provider": "ollama", "model": "qwen3:14b", "enabled": True},
]
ROUTES = {
    "simple_chat": "chat-small",
    "knowledge_learning": "learning-medium",
    "programming": "coding-large",
    "research": "learning-medium",
    "research_review": "learning-medium",
    "assessment": "learning-medium",
}


def setup_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()


def test_each_task_uses_its_assigned_model_profile(tmp_path, monkeypatch):
    setup_settings(tmp_path, monkeypatch)
    db.update_settings({"llm_models": PROFILES, "llm_task_mapping": ROUTES})
    calls = []

    def fake_adapter(profile, messages, options, response_format, timeout):
        calls.append({
            "profile": profile,
            "messages": messages,
            "options": options,
            "response_format": response_format,
            "timeout": timeout,
        })
        return {"message": {"content": "ok"}}

    monkeypatch.setitem(llm._PROVIDER_ADAPTERS, "ollama", fake_adapter)
    for task, profile_id in ROUTES.items():
        response = llm.complete(task, [{"role": "user", "content": task}])
        assert response["message"]["content"] == "ok"
        assert calls[-1]["profile"]["id"] == profile_id
    assert [call["profile"]["model"] for call in calls[:3]] == [
        "qwen2.5:7b", "qwen3:8b", "qwen3:14b"
    ]


def test_legacy_model_is_used_when_model_profiles_have_not_been_configured():
    profile = llm.resolve_model_profile(
        "programming",
        {"llm_models": [], "llm_task_mapping": {}, "ollama_model": "legacy-local-model"},
    )
    assert profile["provider"] == "ollama"
    assert profile["model"] == "legacy-local-model"
    assert profile["id"] == "default-local"


def test_settings_validation_rejects_unknown_task_profile(tmp_path, monkeypatch):
    setup_settings(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unknown model profile"):
        llm.validate_routing_settings(
            {
                "llm_models": PROFILES,
                "llm_task_mapping": {"programming": "missing-profile"},
            },
            db.get_settings(),
        )


def test_settings_validation_rejects_duplicate_profile_ids():
    duplicate = [dict(PROFILES[0]), dict(PROFILES[0])]
    with pytest.raises(ValueError, match="Duplicate model profile id"):
        llm.normalize_model_profiles(duplicate)


def test_chat_chooses_simple_chat_or_programming_role(monkeypatch):
    selected = []
    monkeypatch.setattr(chat_research, "should_use_research", lambda _message: False)
    monkeypatch.setattr(
        chat_research.ollama,
        "chat",
        lambda *args, **kwargs: selected.append(kwargs.get("task")) or "simple response",
    )
    assert chat_research.answer("سلام") == "simple response"
    assert selected[-1] == "simple_chat"

    monkeypatch.setattr(chat_research, "should_use_research", lambda _message: True)
    monkeypatch.setattr(chat_research.web_sources, "discover_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat_research, "_memory_context", lambda _message: "")
    monkeypatch.setattr(chat_research, "_record_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        chat_research.ollama,
        "chat",
        lambda *args, **kwargs: selected.append(kwargs.get("task")) or "generated code answer",
    )
    response = chat_research.answer("Write a Python function to parse JSON.")
    assert "generated code answer" in response
    assert selected[-1] == "programming"
