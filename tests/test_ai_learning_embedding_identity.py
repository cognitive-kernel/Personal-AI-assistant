import pytest

from app.ai_learning.embedding_identity import embedding_cache_identity


def test_embedding_cache_identity_includes_provider_endpoint_and_model():
    ollama_settings = {
        "embedding_llm_provider": "ollama",
        "ollama_base_url": "http://127.0.0.1:11434/",
    }
    openai_settings = {
        "embedding_llm_provider": "openai_compatible",
        "embedding_llm_base_url": "https://api.openai.com/v1/",
    }

    ollama_identity = embedding_cache_identity(ollama_settings, "nomic-embed-text")
    openai_identity = embedding_cache_identity(openai_settings, "nomic-embed-text")

    assert ollama_identity == "ollama|http://127.0.0.1:11434|nomic-embed-text"
    assert openai_identity == "openai_compatible|https://api.openai.com/v1|nomic-embed-text"
    assert ollama_identity != openai_identity


def test_embedding_cache_identity_changes_when_endpoint_changes():
    first = embedding_cache_identity(
        {"embedding_llm_provider": "openai_compatible", "embedding_llm_base_url": "https://one.example/v1"},
        "same-model",
    )
    second = embedding_cache_identity(
        {"embedding_llm_provider": "openai_compatible", "embedding_llm_base_url": "https://two.example/v1"},
        "same-model",
    )
    assert first != second


@pytest.mark.parametrize(
    ("settings", "model"),
    [
        ({"embedding_llm_provider": "unknown"}, "model"),
        ({"embedding_llm_provider": "ollama", "ollama_base_url": ""}, "model"),
        ({"embedding_llm_provider": "ollama"}, ""),
        ({}, "x" * 201),
    ],
)
def test_embedding_cache_identity_rejects_invalid_provider_contract(settings, model):
    with pytest.raises((TypeError, ValueError)):
        embedding_cache_identity(settings, model)
