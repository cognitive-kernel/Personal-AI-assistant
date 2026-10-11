from __future__ import annotations
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
