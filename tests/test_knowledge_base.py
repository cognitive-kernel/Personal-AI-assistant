from __future__ import annotations
import email.message
import urllib.request
import pytest
from app import db
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



def test_pinned_resolver_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr(
        knowledge_base.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        knowledge_base._resolve_pinned_ip("https://learn.microsoft.com/en-us/sql/")


def test_pinned_resolver_returns_only_validated_public_ip(monkeypatch):
    monkeypatch.setattr(
        knowledge_base.socket,
        "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    assert knowledge_base._resolve_pinned_ip("https://learn.microsoft.com/en-us/sql/") == "8.8.8.8"


def test_ingest_and_search_are_idempotent_and_source_attributed(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(
        knowledge_base.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    body = "SQL Server execution plans and indexes explain query performance. " * 12
    monkeypatch.setattr(knowledge_base, "_fetch", lambda url: ("SQL Server docs", body))
    url = "https://learn.microsoft.com/en-us/sql/"
    first = knowledge_base.ingest_url(url)
    second = knowledge_base.ingest_url(url)
    assert first["status"] == "indexed"
    assert first["chunks"] >= 1
    assert second["unchanged"] is True
    results = knowledge_base.search("SQL Server execution plans", limit=5)
    assert results
    assert all(item["url"] == url for item in results)
    assert all(item["retrieval"] == "sqlite_fts5" for item in results)
