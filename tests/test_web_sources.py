import base64
import urllib.parse

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



def test_search_results_fall_back_to_bing_when_duckduckgo_has_no_parseable_results(monkeypatch):
    def fake_open(url, **kwargs):
        if "duckduckgo.com" in url:
            return "<html><body>Search is unavailable</body></html>", "text/html"
        return (
            '<ol><li class="b_algo"><h2><a href="https://example.org/docs">'
            "Official Compute Docs</a></h2></li></ol>",
            "text/html",
        )

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_validate_public_url", lambda url: urllib.parse.urlparse(url)
    )

    assert web_sources._search_results("cloud compute free tier", 3) == [
        ("Official Compute Docs", "https://example.org/docs")
    ]


def test_bing_parser_decodes_organic_result_redirect_urls():
    destination = "https://example.org/docs?section=compute"
    encoded = base64.urlsafe_b64encode(destination.encode()).decode().rstrip("=")
    redirect = f"https://www.bing.com/ck/a?u=a1{encoded}&ntb=1"
    parser = web_sources._BingSearchResultParser()
    parser.feed(
        f'<ol><li class="b_algo"><h2><a href="{redirect}">'
        "Official Compute Docs</a></h2></li></ol>"
    )

    assert parser.results == [("Official Compute Docs", destination)]


def test_search_results_raise_diagnostic_when_all_providers_return_no_results(monkeypatch):
    monkeypatch.setattr(
        web_sources, "_open", lambda _url, **_kwargs: ("<html>blocked</html>", "text/html")
    )
    monkeypatch.setattr(
        web_sources, "_validate_public_url", lambda url: urllib.parse.urlparse(url)
    )

    with pytest.raises(web_sources.SourceFetchError, match="DuckDuckGo:no_parseable_results"):
        web_sources._search_results("cloud compute free tier", 3)



def test_sandbox_search_uses_bounded_alternate_queries_when_primary_is_unparseable(monkeypatch):
    requested_queries = []

    def fake_open(url, **kwargs):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)["q"][0]
        requested_queries.append(query)
        if (
            parsed.hostname == "www.bing.com"
            and query == "free cloud development environment sandbox runtime limits official"
        ):
            return (
                '<ol><li class="b_algo"><h2><a href="https://example.org/sandbox-docs">'
                "Official Sandbox Compute Limits</a></h2></li></ol>",
                "text/html",
            )
        return "<html><body>Search challenge page</body></html>", "text/html"

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_validate_public_url", lambda url: urllib.parse.urlparse(url)
    )

    result = web_sources._search_results(
        "official free developer sandbox container compute limits", 3
    )

    assert result == [("Official Sandbox Compute Limits", "https://example.org/sandbox-docs")]
    assert requested_queries[:2] == [
        "official free developer sandbox container compute limits",
        "official free developer sandbox container compute limits",
    ]
    assert requested_queries[2:] == [
        "free cloud development environment sandbox runtime limits official",
        "free cloud development environment sandbox runtime limits official",
    ]
    assert len(requested_queries) == 4
