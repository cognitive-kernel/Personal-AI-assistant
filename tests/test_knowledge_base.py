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



def test_web_search_filters_results_to_allowlisted_official_docs(monkeypatch):
    class FakeResponse:
        status = 200
        headers = email.message.Message()
        def __init__(self):
            self.headers["Content-Type"] = "text/html; charset=utf-8"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return (
                '<a class="result__a" href="/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fjson.html">Python JSON</a>'
                '<a class="result__a" href="/l/?uddg=https%3A%2F%2Fevil.example%2Fsteal">Untrusted</a>'
            ).encode()

    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == 8
            return FakeResponse()

    monkeypatch.setattr(
        knowledge_base.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(knowledge_base.urllib.request, "build_opener", lambda *_args: FakeOpener())
    urls = knowledge_base._search_web("python json", limit=5)
    assert urls == ["https://docs.python.org/3/library/json.html"]


def test_research_and_index_records_indexing_errors(monkeypatch):
    monkeypatch.setattr(
        knowledge_base, "_search_web",
        lambda query, limit: ["https://docs.python.org/3/library/json.html"],
    )
    monkeypatch.setattr(
        knowledge_base, "ingest_url",
        lambda url: (_ for _ in ()).throw(ValueError("test fetch failure")),
    )
    result = knowledge_base.research_and_index("python json")
    assert result["provider"] == "DuckDuckGo HTML"
    assert result["indexed"] == []
    assert result["errors"][0]["url"] == "https://docs.python.org/3/library/json.html"



def test_local_relevance_gate_rejects_single_common_token_match():
    weak = {"title": "General documentation", "content": "This page explains general software behavior and configuration."}
    strong = {"title": "Python JSON decoder", "content": "The Python JSON decoder raises an error for malformed JSON input."}
    assert knowledge_base.is_relevant("Python JSON decoder error", weak) is False
    assert knowledge_base.is_relevant("Python JSON decoder error", strong) is True


def test_local_relevance_gate_uses_cross_language_variants():
    source = {"title": "SQL Server execution plan", "content": "Query optimizer chooses an execution plan and index access path."}
    assert knowledge_base.is_relevant(
        "طرح اجرای پرس‌وجو",
        source,
        ["SQL Server query execution plan"],
    ) is True
