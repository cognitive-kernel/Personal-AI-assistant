import pytest

from app import db, learning, web_sources


def test_search_result_parser_extracts_duckduckgo_redirect_target():
    parser = web_sources._SearchResultParser()
    parser.feed(
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fexample.org%2Fdocs%3Fx%3D1">Official Docs</a>'
    )

    assert parser.results == [("Official Docs", "https://example.org/docs?x=1")]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/",
        "http://user:secret@example.org/",
        "file:///etc/passwd",
        "https://example.org:8443/",
        "https://kaggle.com/docs/notebooks",
        "https://www.kaggle.com/code/example/notebook",
    ],
)
def test_url_validation_blocks_private_or_unsupported_destinations(url):
    with pytest.raises(web_sources.SourceFetchError):
        web_sources._validate_public_url(url)


def test_page_text_parser_ignores_script_navigation_and_styles():
    parser = web_sources._PageTextParser()
    parser.feed(
        "<html><head><title>Guide</title><style>hidden style</style></head>"
        "<body><nav>navigation</nav><h1>Heading</h1>"
        "<p>Useful technical content.</p><script>secret token</script></body></html>"
    )
    text = " ".join("".join(parser.text_parts).split())

    assert "Useful technical content." in text
    assert "Heading" in text
    assert "navigation" not in text
    assert "hidden style" not in text
    assert "secret token" not in text
    assert "Guide" in "".join(parser.title_parts)


def test_discovery_skips_unavailable_pages_and_returns_available_source(monkeypatch):
    expected = web_sources.SourceDocument(
        url="https://example.org/guide",
        title="Guide",
        text="Useful source text " * 10,
    )
    monkeypatch.setattr(
        web_sources, "_search_results",
        lambda _query, _limit: [
            ("Unavailable", "https://unavailable.example/"),
            ("Guide", "https://example.org/guide"),
        ],
    )

    def fetch(url, title):
        if "unavailable" in url:
            raise web_sources.SourceFetchError("unavailable")
        return expected

    monkeypatch.setattr(web_sources, "_fetch_document", fetch)

    assert web_sources.discover_sources("test topic", limit=2) == [expected]


def test_web_sources_are_saved_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    source = web_sources.SourceDocument(
        url="https://example.org/python-guide",
        title="Python Guide",
        text="Python source material " * 40,
    )
    monkeypatch.setattr(web_sources, "discover_sources", lambda _query, limit=3: [source])
    job = learning.create_job("Python source discovery")

    content, source_kind = learning._discover_and_store_sources(job["id"], job["topic"])

    saved = learning.get_job(job["id"])
    assert source_kind == "web_retrieved"
    assert "Source URL: https://example.org/python-guide" in content
    assert saved["source_kind"] == "web_retrieved"
    assert any(
        item["evidence_type"] == "web_page"
        and item["source_ref"] == source.url
        and item["content_hash"]
        for item in saved["evidence"]
    )
    with db.learning_connection() as conn:
        stored = conn.execute(
            "SELECT content FROM learning_source_text WHERE job_id=?", (job["id"],)
        ).fetchone()
    assert stored["content"] == content


def test_web_discovery_failure_is_recorded_without_blocking_model_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(web_sources, "discover_sources", lambda _query, limit=3: [])
    job = learning.create_job("Offline learning topic")

    content, source_kind = learning._discover_and_store_sources(job["id"], job["topic"])

    saved = learning.get_job(job["id"])
    assert content == ""
    assert source_kind == "model_knowledge_no_external_source"
    assert any(item["evidence_type"] == "web_search" for item in saved["evidence"])



def test_official_kaggle_cli_docs_are_not_blocked_by_kaggle_page_fetch_policy(monkeypatch):
    import socket

    # Keep this unit test deterministic; it does not make a real network request.
    monkeypatch.setattr(
        web_sources.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("140.82.112.4", 443))
        ],
    )
    # The official CLI docs are hosted on GitHub; the block is for fetching
    # Kaggle's own web properties, not for the provider's published source code.
    parsed = web_sources._validate_public_url(
        "https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md"
    )
    assert parsed.hostname == "github.com"
