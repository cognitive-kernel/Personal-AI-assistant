import json
import socket

import pytest

from app import learning_review_adapters


def _provider(**overrides):
    value = {
        "provider_id": "provider_a",
        "enabled": True,
        "data_sharing_approved": True,
        "base_url": "https://api.example.com/v1",
        "host_allowlist": ["api.example.com"],
        "model": "model-name",
        "model_revision": "pinned-revision",
        "api_key_env": "LEARNING_REVIEW_API_KEY_PROVIDER_A",
    }
    value.update(overrides)
    return value


def test_external_reviewers_require_explicit_data_sharing_approval():
    assert learning_review_adapters._external_configs({
        "learning_external_review_providers": [_provider(data_sharing_approved=False)]
    }) == []


@pytest.mark.parametrize("overrides", [
    {"base_url": "http://api.example.com/v1"},
    {"base_url": "https://api.example.com.evil.test/v1", "host_allowlist": ["api.example.com"]},
    {"api_key_env": "OPENAI_API_KEY"},
    {"provider_id": "ollama"},
    {"base_url": "https://api.example.com/v1?redirect=https://evil.test"},
])
def test_external_reviewers_reject_unsafe_provider_configuration(overrides):
    assert learning_review_adapters._external_configs({
        "learning_external_review_providers": [_provider(**overrides)]
    }) == []


def test_reviewer_selection_prefers_external_provider_diversity():
    settings = {"learning_external_review_providers": [_provider()]}
    reviewers = learning_review_adapters.list_reviewers(
        settings,
        [{"name": "local-reviewer", "digest": "a" * 64}],
    )
    assert len(reviewers) == 2
    assert {item["provider_id"] for item in reviewers} == {"provider_a", "ollama"}


def test_external_reviewer_blocks_private_dns_results(monkeypatch):
    provider = learning_review_adapters._external_configs({
        "learning_external_review_providers": [_provider()]
    })[0]
    monkeypatch.setattr(
        "app.learning_review_adapters.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(learning_review_adapters.ReviewProviderUnavailable, match="non-public IP"):
        learning_review_adapters._assert_public_provider_host(provider)


def test_external_reviewer_requires_system_fingerprint(monkeypatch):
    provider = learning_review_adapters._external_configs({
        "learning_external_review_providers": [_provider()]
    })[0]
    monkeypatch.setenv("LEARNING_REVIEW_API_KEY_PROVIDER_A", "test-key")
    monkeypatch.setattr(
        "app.learning_review_adapters.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )

    class Response:
        is_redirect = False
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self):
            yield json.dumps({
                "model": "model-name",
                "choices": [{"message": {"content": '{"verdict":"supports"}'}}],
            }).encode()

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def stream(self, *_args, **_kwargs): return Response()

    monkeypatch.setattr("app.learning_review_adapters.httpx.Client", Client)
    with pytest.raises(learning_review_adapters.ReviewProviderUnavailable, match="system_fingerprint"):
        learning_review_adapters.run_reviewer(provider, "{}", system_prompt="Review conservatively.")



def test_external_reviewer_records_actual_model_fingerprint(monkeypatch):
    provider = learning_review_adapters._external_configs({
        "learning_external_review_providers": [_provider()]
    })[0]
    monkeypatch.setenv("LEARNING_REVIEW_API_KEY_PROVIDER_A", "test-key")
    monkeypatch.setattr(
        "app.learning_review_adapters.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )

    class Response:
        is_redirect = False
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self):
            yield json.dumps({
                "model": "model-name-2026-10",
                "system_fingerprint": "fp-immutable-build-7",
                "usage": {"prompt_tokens": 120, "completion_tokens": 80},
                "choices": [{"message": {"content": '{"verdict":"supports","rationale":"The supplied evidence supports the lesson."}'}}],
            }).encode()

    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def stream(self, *_args, **_kwargs): return Response()

    monkeypatch.setattr("app.learning_review_adapters.httpx.Client", Client)
    result = learning_review_adapters.run_reviewer(
        provider, '{"claim":"example"}', system_prompt="Review the claim conservatively."
    )
    assert result["provider_id"] == "provider_a"
    assert result["model"] == "model-name-2026-10"
    assert result["digest"] != provider["config_digest"]
    assert result["system_fingerprint"] == "fp-immutable-build-7"
