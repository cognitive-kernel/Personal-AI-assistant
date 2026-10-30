import json
import socket
import urllib.request

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


def _mock_external_opener(monkeypatch, body, captured):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, limit):
            captured["read_limit"] = limit
            return body

    class Opener:
        def open(self, request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(
        "app.learning_review_adapters.urllib.request.build_opener", build_opener
    )


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
    {"base_url": "https://api.example.com:invalid/v1"},
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
    captured = {}
    _mock_external_opener(
        monkeypatch,
        json.dumps({
            "model": "model-name",
            "choices": [{"message": {"content": '{"verdict":"supports"}'}}],
        }).encode(),
        captured,
    )

    with pytest.raises(learning_review_adapters.ReviewProviderUnavailable, match="system_fingerprint"):
        learning_review_adapters.run_reviewer(provider, "{}", system_prompt="Review conservatively.")


def test_external_reviewer_records_fingerprint_and_uses_validated_pinned_ip(monkeypatch):
    provider = learning_review_adapters._external_configs({
        "learning_external_review_providers": [_provider()]
    })[0]
    monkeypatch.setenv("LEARNING_REVIEW_API_KEY_PROVIDER_A", "test-key")
    dns_calls = []

    def resolve_public(*args, **kwargs):
        dns_calls.append((args, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(
        "app.learning_review_adapters.socket.getaddrinfo", resolve_public
    )
    captured = {}
    _mock_external_opener(
        monkeypatch,
        json.dumps({
            "model": "model-name-2026-10",
            "system_fingerprint": "fp-immutable-build-7",
            "usage": {"prompt_tokens": 120, "completion_tokens": 80},
            "choices": [{"message": {"content": '{"verdict":"supports","rationale":"The supplied evidence supports the lesson."}'}}],
        }).encode(),
        captured,
    )

    result = learning_review_adapters.run_reviewer(
        provider, '{"claim":"example"}', system_prompt="Review the claim conservatively."
    )
    assert result["provider_id"] == "provider_a"
    assert result["model"] == "model-name-2026-10"
    assert result["digest"] != provider["config_digest"]
    assert result["system_fingerprint"] == "fp-immutable-build-7"
    assert result["usage"] == {"prompt_tokens": 120, "completion_tokens": 80}

    handlers = captured["handlers"]
    pinned = next(
        handler for handler in handlers
        if isinstance(handler, learning_review_adapters.PinnedHTTPSHandler)
    )
    request = captured["request"]
    assert pinned._resolver(request.full_url) == "8.8.8.8"
    assert len(dns_calls) == 1
    assert captured["timeout"] == 45.0
    assert captured["read_limit"] == 65537
    assert request.get_method() == "POST"
    assert "Bearer test-key" == request.get_header("Authorization")
    assert any(
        isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {}
        for handler in handlers
    )
    assert any(
        isinstance(handler, learning_review_adapters._NoRedirectHandler)
        for handler in handlers
    )


def test_external_reviewer_redirect_handler_never_returns_redirect_request():
    handler = learning_review_adapters._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


