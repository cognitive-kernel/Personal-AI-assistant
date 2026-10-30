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

def test_safe_redirect_allows_policy_approved_official_host(monkeypatch):
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


def test_knowledge_fetch_disables_proxies_and_resolves_only_at_pinned_connect(monkeypatch):
    dns_calls = []

    def resolve(*args, **kwargs):
        dns_calls.append((args, kwargs))
        return [(None, None, None, None, ("8.8.8.8", 443))]

    monkeypatch.setattr(knowledge_base.socket, "getaddrinfo", resolve)
    captured = {}
    body = (
        "<html><title>Official docs</title><body>"
        + "<p>SQL Server documentation describes query processing, indexes, execution plans, "
          "transactions, security, and administration. </p>" * 8
        + "</body></html>"
    ).encode("utf-8")

    class Response:
        status = 200

        def __init__(self):
            self.headers = email.message.Message()
            self.headers["Content-Type"] = "text/html; charset=utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            captured["read_limit"] = limit
            return body[:limit]

    class Opener:
        def open(self, request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(knowledge_base.urllib.request, "build_opener", build_opener)

    title, text = knowledge_base._fetch("https://learn.microsoft.com/en-us/sql/")

    assert title == "Official docs"
    assert len(text) >= 200
    assert captured["timeout"] == knowledge_base._TIMEOUT_SECONDS
    assert captured["read_limit"] == knowledge_base._MAX_BYTES + 1
    assert captured["request"].get_method() == "GET"
    assert len(dns_calls) == 0, "DNS must not be resolved before the transport is ready"

    pinned = next(
        handler for handler in captured["handlers"]
        if isinstance(handler, knowledge_base.PinnedHTTPSHandler)
    )
    assert pinned._resolver(captured["request"].full_url) == "8.8.8.8"
    assert len(dns_calls) == 1, "the pinned transport must validate DNS exactly once"

    proxies = [
        handler for handler in captured["handlers"]
        if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert len(proxies) == 1
    assert proxies[0].proxies == {}
    assert any(
        isinstance(handler, knowledge_base._SafeRedirect)
        for handler in captured["handlers"]
    )


@pytest.mark.parametrize("url", ["x" * 2049, None, 123])
def test_knowledge_url_policy_rejects_non_text_or_oversized_url(url):
    with pytest.raises(ValueError):
        knowledge_base._parse_url_policy(url)
