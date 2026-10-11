from __future__ import annotations
import email.message
import urllib.request
import pytest
from app import knowledge_base

@pytest.mark.parametrize("url", [
    "http://learn.microsoft.com/en-us/sql/", "https://example.com/page",
    "https://learn.microsoft.com.evil.example/page", "https://user:pass@learn.microsoft.com/en-us/sql/",
    "https://learn.microsoft.com:8443/en-us/sql/", "file:///etc/passwd",
])
def test_url_policy_rejects_unapproved_urls(monkeypatch, url):
    monkeypatch.setattr(knowledge_base.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))])
    with pytest.raises(ValueError): knowledge_base._validate_url(url)

def test_chunks_overlap_and_cover_content():
    chunks = knowledge_base._chunks("A" * 2200)
    assert len(chunks) >= 2
    assert chunks[0][-knowledge_base._OVERLAP_CHARS:] == chunks[1][:knowledge_base._OVERLAP_CHARS]

def test_extractor_ignores_script_and_navigation():
    parser = knowledge_base._TextExtractor()
    parser.feed("<html><title>Docs</title><nav>menu</nav><p>Useful content here.</p><script>secret()</script></html>")
    text = "".join(parser.parts)
    assert "Useful content here." in text and "menu" not in text and "secret" not in text
    assert "".join(parser.title_parts) == "Docs"

def test_safe_redirect_allows_validated_official_host(monkeypatch):
    monkeypatch.setattr(
        knowledge_base.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    handler = knowledge_base._SafeRedirect()
    request = urllib.request.Request("https://learn.microsoft.com/en-us/sql/")
    redirected = handler.redirect_request(
        request, None, 302, "Found", email.message.Message(),
        "https://learn.microsoft.com/en-us/sql/overview",
    )
    assert redirected is not None
    assert redirected.full_url == "https://learn.microsoft.com/en-us/sql/overview"


def test_safe_redirect_rejects_unapproved_host(monkeypatch):
    monkeypatch.setattr(
        knowledge_base.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    handler = knowledge_base._SafeRedirect()
    request = urllib.request.Request("https://learn.microsoft.com/en-us/sql/")
    redirected = handler.redirect_request(
        request, None, 302, "Found", email.message.Message(),
        "https://example.com/redirect-target",
    )
    assert redirected is None


def test_safe_redirect_caps_redirect_chain(monkeypatch):
    monkeypatch.setattr(
        knowledge_base.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    handler = knowledge_base._SafeRedirect()
    request = urllib.request.Request("https://learn.microsoft.com/en-us/sql/")
    request.redirect_dict = {"one": 1, "two": 1, "three": 1}
    redirected = handler.redirect_request(
        request, None, 302, "Found", email.message.Message(),
        "https://learn.microsoft.com/en-us/sql/overview",
    )
    assert redirected is None
