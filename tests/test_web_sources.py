import base64
import email.message
import urllib.parse
import urllib.request

import pytest

from app import db, learning, web_sources


def test_search_result_parser_extracts_duckduckgo_redirect_target():
    parser = web_sources._SearchResultParser()
    parser.feed(
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fexample.org%2Fdocs%3Fx%3D1">Official Docs</a>'
    )

    assert parser.results == [("Official Docs", "https://example.org/docs?x=1")]


def test_duckduckgo_redirect_preserves_encoded_target_query_parameters(monkeypatch):
    parser = web_sources._SearchResultParser()
    parser.feed(
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fexample.org%2Fsearch%3Fq%3Dalpha%252Bbeta%2526mode%253Dstrict">'
        'Encoded target</a>'
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [])

    normalized = web_sources._normalize_search_results(
        parser.results,
        limit=1,
        provider_hosts=web_sources.SEARCH_PROVIDER_HOSTS,
    )

    assert normalized == [
        ("Encoded target", "https://example.org/search?q=alpha%2Bbeta%26mode%3Dstrict")
    ]


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
        "https://example.org:/",
        "https://example.org:",
    ],
)
def test_url_validation_blocks_private_or_unsupported_destinations(url):
    with pytest.raises(web_sources.SourceFetchError):
        web_sources._validate_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://@example.org/",
        "https://:secret@example.org/",
        "https://user:@example.org/",
    ],
)
def test_url_parser_rejects_any_userinfo_component(url):
    with pytest.raises(web_sources.SourceFetchError, match="credentials"):
        web_sources._parse_public_url(url)


def test_parse_public_url_wraps_malformed_ipv6_authority():
    with pytest.raises(web_sources.SourceFetchError, match="syntax"):
        web_sources._parse_public_url("https://[not-an-ip]/")


def test_normalize_search_results_skips_malformed_authority(monkeypatch):
    monkeypatch.setattr(
        web_sources.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    results = web_sources._normalize_search_results(
        [
            ("Malformed", "https://[not-an-ip]/"),
            ("Valid", "https://example.org/docs"),
        ],
        limit=3,
        provider_hosts=web_sources.SEARCH_PROVIDER_HOSTS,
    )
    assert results == [("Valid", "https://example.org/docs")]


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


def test_fetch_document_preserves_block_boundaries_for_relevance_filter(monkeypatch):
    html = (
        "<html><head><title>Explore Free Azure Services</title></head><body>"
        "<main><h1>Explore Free Azure Services</h1>"
        "<p>These services are free up to the specified monthly amounts.</p>"
        "<p>Linux virtual machines are available in Azure.</p>"
        "<p>Get access to free developer tools and a USD 100 credit.</p>"
        "</main></body></html>"
    )
    monkeypatch.setattr(
        web_sources, "_open", lambda _url, max_bytes: (html, "text/html")
    )

    document = web_sources._fetch_document(
        "https://azure.microsoft.com/en-us/pricing/free-services/",
        "Explore Free Azure Services",
    )

    lines = document.text.splitlines()
    assert any("free up to the specified monthly amounts" in line for line in lines)
    assert any("Linux virtual machines are available" in line for line in lines)
    assert any("free developer tools" in line for line in lines)


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
    repeated_content, repeated_kind = learning._discover_and_store_sources(job["id"], job["topic"])

    saved = learning.get_job(job["id"])
    assert source_kind == repeated_kind == "web_retrieved"
    assert repeated_content == content
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
    with db.learning_connection() as conn:
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM learning_evidence WHERE job_id=? AND evidence_type='web_page'",
            (job["id"],),
        ).fetchone()[0]
    assert evidence_count == 1


def test_web_discovery_failure_is_recorded_without_blocking_model_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db.defaults, "settings_db_path", str(tmp_path / "settings.db"))
    monkeypatch.setattr(db.defaults, "learning_db_path", str(tmp_path / "learning.db"))
    db.init_databases()
    monkeypatch.setattr(web_sources, "discover_sources", lambda _query, limit=3: [])
    job = learning.create_job("Offline learning topic")

    content, source_kind = learning._discover_and_store_sources(job["id"], job["topic"])
    repeated_content, repeated_kind = learning._discover_and_store_sources(job["id"], job["topic"])

    saved = learning.get_job(job["id"])
    assert content == repeated_content == ""
    assert source_kind == repeated_kind == "model_knowledge_no_external_source"
    assert source_kind == "model_knowledge_no_external_source"
    assert sum(item["evidence_type"] == "web_search" for item in saved["evidence"]) == 1



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
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    assert web_sources._search_results("cloud compute free tier", 3) == [
        ("Official Compute Docs", "https://example.org/docs")
    ]


def test_google_parser_extracts_h3_result_and_decodes_redirect():
    parser = web_sources._GoogleSearchResultParser()
    parser.feed(
        '<a href="/url?q=https%3A%2F%2Fexample.org%2Fdocs%3Futm_source%3Dgoogle">'
        '<h3>Official Compute Docs</h3></a>'
    )

    assert parser.results == [
        ("Official Compute Docs", "https://example.org/docs?utm_source=google")
    ]


def test_search_results_aggregate_google_bing_and_duckduckgo_with_dedup(monkeypatch):
    pages = {
        "html.duckduckgo.com": (
            '<a class="result__a" href="https://example.org/docs?utm_source=ddg#part">'
            "Official Compute Docs</a>"
            '<a class="result__a" href="https://ddg.example.org/guide">DDG Guide</a>'
        ),
        "www.bing.com": (
            '<ol><li class="b_algo"><h2><a href="https://www.example.org/docs#top">'
            "Duplicate Compute Docs</a></h2></li>"
            '<li class="b_algo"><h2><a href="https://bing.example.org/guide">'
            "Bing Guide</a></h2></li></ol>"
        ),
        "www.google.com": (
            '<a href="/url?q=https%3A%2F%2Fexample.org%2Fdocs%3Fved%3D123">'
            "<h3>Duplicate Compute Docs from Google</h3></a>"
            '<a href="https://google.example.org/guide"><h3>Google Guide</h3></a>'
        ),
    }

    def fake_open(url, **kwargs):
        host = urllib.parse.urlparse(url).hostname
        return pages[host], "text/html"

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    results = web_sources._search_results("cloud compute terms", 3)

    assert results == [
        ("Official Compute Docs", "https://example.org/docs"),
        ("DDG Guide", "https://ddg.example.org/guide"),
        ("Bing Guide", "https://bing.example.org/guide"),
        ("Google Guide", "https://google.example.org/guide"),
    ]
    assert len({web_sources.result_url_identity(url) for _, url in results}) == len(results)


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


def test_search_results_round_robin_unique_results_across_engines(monkeypatch):
    pages = {
        "html.duckduckgo.com": "".join(
            f'<a class="result__a" href="https://ddg.example.org/item-{i}">DDG {i}</a>'
            for i in range(1, 4)
        ),
        "www.bing.com": (
            "<ol>"+"".join(
                f'<li class="b_algo"><h2><a href="https://bing.example.org/item-{i}">Bing {i}</a></h2></li>'
                for i in range(1, 4)
            )+"</ol>"
        ),
        "www.google.com": "".join(
            f'<a href="https://google.example.org/item-{i}"><h3>Google {i}</h3></a>'
            for i in range(1, 4)
        ),
    }

    def fake_open(url, **kwargs):
        return pages[urllib.parse.urlparse(url).hostname], "text/html"

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    results = web_sources._search_results("free cloud hosting", 3)

    assert [url for _, url in results[:6]] == [
        "https://ddg.example.org/item-1",
        "https://bing.example.org/item-1",
        "https://google.example.org/item-1",
        "https://ddg.example.org/item-2",
        "https://bing.example.org/item-2",
        "https://google.example.org/item-2",
    ]


def test_duckduckgo_lite_fallback_is_used_when_primary_has_no_results(monkeypatch):
    def fake_open(url, **kwargs):
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname == "html.duckduckgo.com":
            return "<html>no organic links</html>", "text/html"
        if parsed.hostname == "lite.duckduckgo.com":
            return (
                '<a class="result-link" href="https://lite.example.org/free-vm">'
                "Free VM Compute</a>",
                "text/html",
            )
        return "<html>no organic links</html>", "text/html"

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    results = web_sources._search_results("free cloud hosting", 3)
    diagnostics = {item["provider"]: item for item in results.provider_diagnostics}
    assert ("Free VM Compute", "https://lite.example.org/free-vm") in results
    assert diagnostics["DuckDuckGo"]["attempts"][0]["error"] == "no_parseable_results"
    assert diagnostics["DuckDuckGo"]["attempts"][1]["endpoint_variant"] == "lite"
    assert diagnostics["DuckDuckGo"]["attempts"][1]["accepted_result_count"] == 1


def test_google_basic_html_fallback_is_used_when_standard_markup_has_no_results(monkeypatch):
    def fake_open(url, **kwargs):
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.hostname == "www.google.com" and params.get("gbv") == ["1"]:
            return (
                '<a href="/url?q=https%3A%2F%2Fgoogle.example.org%2Fcompute">'
                "<h3>Google Free Compute</h3></a>",
                "text/html",
            )
        if parsed.hostname == "www.google.com":
            return "<html><body>Before you continue to Google</body></html>", "text/html"
        return "<html><body>no organic links</body></html>", "text/html"

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    results = web_sources._search_results("free cloud hosting", 3)
    diagnostics = {item["provider"]: item for item in results.provider_diagnostics}
    google = diagnostics["Google"]
    assert ("Google Free Compute", "https://google.example.org/compute") in results
    assert google["attempts"][0]["error"] == "challenge_or_consent_page"
    assert google["attempts"][1]["endpoint_variant"] == "basic_html"
    assert google["attempts"][1]["accepted_result_count"] == 1


def test_search_results_include_per_engine_counts_and_duplicate_counts(monkeypatch):
    pages = {
        "html.duckduckgo.com": (
            '<a class="result__a" href="https://example.org/docs">Official Docs</a>'
            '<a class="result__a" href="https://ddg.example.org/guide">DDG Guide</a>'
        ),
        "www.bing.com": (
            '<ol><li class="b_algo"><h2><a href="https://www.example.org/docs">'
            "Duplicate Official Docs</a></h2></li>"
            '<li class="b_algo"><h2><a href="https://bing.example.org/guide">'
            "Bing Guide</a></h2></li></ol>"
        ),
        "www.google.com": (
            '<a href="/url?q=https%3A%2F%2Fexample.org%2Fdocs">'
            "<h3>Duplicate Official Docs</h3></a>"
            '<a href="https://google.example.org/guide"><h3>Google Guide</h3></a>'
        ),
    }

    def fake_open(url, **kwargs):
        host = urllib.parse.urlparse(url).hostname
        return pages[host], "text/html"

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    results = web_sources._search_results("cloud compute terms", 3)

    assert len(results) == 4
    diagnostics = {item["provider"]: item for item in results.provider_diagnostics}
    assert diagnostics["DuckDuckGo"]["parsed_result_count"] == 2
    assert diagnostics["Bing"]["accepted_result_count"] == 2
    assert diagnostics["Bing"]["duplicate_result_count"] == 1
    assert diagnostics["Google"]["accepted_result_count"] == 2
    assert diagnostics["Google"]["duplicate_result_count"] == 1
    assert diagnostics["Google"]["unique_result_count"] == 1


def test_successful_search_keeps_diagnostics_when_one_provider_fails(monkeypatch):
    def fake_open(url, **kwargs):
        host = urllib.parse.urlparse(url).hostname
        if host == "www.google.com":
            raise web_sources.SourceFetchError("Google temporarily unavailable")
        if host == "www.bing.com":
            return "<html><body>challenge page</body></html>", "text/html"
        return (
            '<a class="result__a" href="https://example.org/compute">'
            "Official Compute Resource</a>",
            "text/html",
        )

    monkeypatch.setattr(web_sources, "_open", fake_open)
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    results = web_sources._search_results("cloud compute terms", 3)
    diagnostics = {item["provider"]: item for item in results.provider_diagnostics}
    assert diagnostics["DuckDuckGo"]["unique_result_count"] == 1
    assert diagnostics["Bing"]["attempts"][0]["error"] == "no_parseable_results"
    assert diagnostics["Google"]["attempts"][0]["error"] == "SourceFetchError"


def test_search_results_raise_diagnostic_when_all_providers_return_no_results(monkeypatch):
    monkeypatch.setattr(
        web_sources, "_open", lambda _url, **_kwargs: ("<html>blocked</html>", "text/html")
    )
    monkeypatch.setattr(
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    with pytest.raises(web_sources.SourceFetchError, match="DuckDuckGo:no_parseable_results"):
        web_sources._search_results("cloud compute free tier", 3)



def test_sandbox_search_uses_bounded_alternate_queries_when_primary_is_unparseable(monkeypatch):
    requested = []

    def fake_open(url, **kwargs):
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)
        query = query_params["q"][0]
        requested.append((parsed.hostname, query_params, query))
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
        web_sources, "_parse_public_url", lambda url: urllib.parse.urlparse(url)
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [True])

    result = web_sources._search_results(
        "official free developer sandbox container compute limits", 3
    )

    assert result == [("Official Sandbox Compute Limits", "https://example.org/sandbox-docs")]
    primary_query = "official free developer sandbox container compute limits"
    alternate_query = "free cloud development environment sandbox runtime limits official"
    assert [q for _, _, q in requested].count(primary_query) == 5
    assert [q for _, _, q in requested].count(alternate_query) == 5
    assert any(
        host == "lite.duckduckgo.com" and query_params.get("kl") == ["us-en"]
        for host, query_params, _ in requested
    )
    assert any(
        host == "www.google.com" and query_params.get("gbv") == ["1"]
        for host, query_params, _ in requested
    )
    assert len(requested) == 10


def test_url_validation_rejects_hostname_with_mixed_public_and_private_dns(monkeypatch):
    monkeypatch.setattr(
        web_sources.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.7", 443)),
        ],
    )
    with pytest.raises(web_sources.SourceFetchError, match="non-public"):
        web_sources._validate_public_url("https://mixed-dns.example/docs")
    with pytest.raises(web_sources.SourceFetchError, match="non-public"):
        web_sources._resolve_public_ip("https://mixed-dns.example/docs")


def test_redirect_handler_rejects_unsafe_url_policy_before_transport():
    handler = web_sources._SafeRedirectHandler()
    request = urllib.request.Request("https://public.example/docs")
    with pytest.raises(web_sources.SourceFetchError):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://user:secret@public.example/redirect"
        )


def test_redirect_handler_rejects_https_downgrade_before_transport():
    handler = web_sources._SafeRedirectHandler()
    request = urllib.request.Request("https://public.example/docs")
    with pytest.raises(
        web_sources.SourceFetchError,
        match="HTTPS is required for research source redirects",
    ):
        handler.redirect_request(
            request, None, 302, "Found", {}, "http://public.example/insecure"
        )


def test_redirect_handler_allows_https_redirects():
    handler = web_sources._SafeRedirectHandler()
    request = urllib.request.Request("https://public.example/docs")
    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://cdn.public.example/docs"
    )
    assert redirected is not None
    assert redirected.full_url == "https://cdn.public.example/docs"


def test_pinned_resolver_rejects_private_redirect_target():
    with pytest.raises(web_sources.SourceFetchError):
        web_sources._resolve_public_ip("http://169.254.169.254/latest/meta-data/")


def test_public_source_resolver_returns_a_validated_literal_for_ip_pinning(monkeypatch):
    monkeypatch.setattr(
        web_sources.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ],
    )
    assert web_sources._resolve_public_ip("https://public.example/docs") == "93.184.216.34"


def test_source_fetch_resolves_dns_once_at_pinned_transport_and_disables_proxies(monkeypatch):
    dns_calls = []

    def resolve(*args, **kwargs):
        dns_calls.append((args, kwargs))
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(web_sources.socket, "getaddrinfo", resolve)
    captured = {}
    body = b"<html><title>Docs</title><p>Safe source content.</p></html>"

    class Response:
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

    monkeypatch.setattr(web_sources.urllib.request, "build_opener", build_opener)

    content, content_type = web_sources._open("https://public.example/docs")

    assert "Safe source content." in content
    assert content_type == "text/html"
    assert captured["timeout"] == web_sources.DEFAULT_TIMEOUT_SECONDS
    assert captured["read_limit"] == web_sources.MAX_RESPONSE_BYTES + 1
    assert dns_calls == [], "URL policy parsing must not perform a preliminary DNS lookup"

    pinned = next(
        handler for handler in captured["handlers"]
        if isinstance(handler, web_sources.PinnedHTTPSHandler)
    )
    assert pinned._resolver(captured["request"].full_url) == "93.184.216.34"
    assert len(dns_calls) == 1, "the actual pinned request must perform exactly one DNS lookup"

    proxies = [
        handler for handler in captured["handlers"]
        if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert len(proxies) == 1 and proxies[0].proxies == {}

def test_discover_sources_records_bounded_fetch_failure_diagnostics(monkeypatch):
    candidates = web_sources.SearchResultList(
        [("First result", "https://example.org/first"), ("Second result", "https://example.org/second")],
        provider_diagnostics=[{
            "provider": "DuckDuckGo",
            "attempts": [{"variant": "primary", "endpoint_variant": "html", "error": None}],
            "parsed_result_count": 2,
            "accepted_result_count": 2,
            "unique_result_count": 2,
            "duplicate_result_count": 0,
        }],
    )
    monkeypatch.setattr(web_sources, "_search_results", lambda *_args, **_kwargs: candidates)

    def reject(*_args, **_kwargs):
        raise web_sources.SourceFetchError("private URL and response details must not be retained")

    monkeypatch.setattr(web_sources, "_fetch_document", reject)
    documents = web_sources.discover_sources("diagnostic test", limit=2)

    assert documents == []
    assert documents.search_provider_diagnostics == candidates.provider_diagnostics
    assert documents.fetch_diagnostics == {
        "candidate_count": 2,
        "attempted_count": 2,
        "fetched_count": 0,
        "failure_counts": {"SourceFetchError": 2},
    }


def test_discover_sources_counts_successful_fetches(monkeypatch):
    candidates = web_sources.SearchResultList([
        ("First result", "https://example.org/first"),
        ("Second result", "https://example.org/second"),
    ])
    document = web_sources.SourceDocument(
        url="https://example.org/first",
        title="First result",
        text="A sufficiently long source excerpt for a deterministic retrieval test. " * 2,
    )
    monkeypatch.setattr(web_sources, "_search_results", lambda *_args, **_kwargs: candidates)
    monkeypatch.setattr(web_sources, "_fetch_document", lambda *_args, **_kwargs: document)

    documents = web_sources.discover_sources("diagnostic test", limit=1)

    assert documents == [document]
    assert documents.fetch_diagnostics == {
        "candidate_count": 2,
        "attempted_count": 1,
        "fetched_count": 1,
        "failure_counts": {},
    }

def test_search_result_normalization_skips_http_before_applying_result_limit(monkeypatch):
    monkeypatch.setattr(
        web_sources,
        "_parse_public_url",
        lambda url: urllib.parse.urlparse(url),
    )
    monkeypatch.setattr(web_sources, "_validated_public_addresses", lambda _parsed: [])

    results = web_sources._normalize_search_results(
        [
            ("Insecure HTTP result", "http://insecure.example/docs"),
            ("Secure HTTPS result", "https://secure.example/docs"),
        ],
        limit=1,
        provider_hosts=(),
    )

    assert results == [("Secure HTTPS result", "https://secure.example/docs")]


def test_research_document_fetch_rejects_http_before_network_access(monkeypatch):
    def unexpected_open(*_args, **_kwargs):
        raise AssertionError("HTTP source must be rejected before network access")

    monkeypatch.setattr(web_sources, "_open", unexpected_open)

    with pytest.raises(web_sources.SourceFetchError, match="HTTPS is required"):
        web_sources._fetch_document("http://example.org/guide", "Guide")


def test_discovery_skips_http_candidates_and_keeps_https_source(monkeypatch):
    html = (
        "<html><head><title>Official Guide</title></head><body>"
        "<main><h1>Official Guide</h1>"
        "<p>" + ("Verified public technical documentation. " * 8) + "</p>"
        "</main></body></html>"
    )
    monkeypatch.setattr(
        web_sources,
        "_search_results",
        lambda _query, _limit: [
            ("HTTP mirror", "http://mirror.example.org/guide"),
            ("Official HTTPS guide", "https://docs.example.org/guide"),
        ],
    )
    monkeypatch.setattr(
        web_sources,
        "_open",
        lambda url, **_kwargs: (html, "text/html")
        if url == "https://docs.example.org/guide"
        else (_ for _ in ()).throw(AssertionError("HTTP candidate reached transport")),
    )

    documents = web_sources.discover_sources("technical documentation", limit=1)

    assert len(documents) == 1
    assert documents[0].url == "https://docs.example.org/guide"
    assert documents.fetch_diagnostics["attempted_count"] == 2
    assert documents.fetch_diagnostics["fetched_count"] == 1
    assert documents.fetch_diagnostics["failure_counts"] == {"SourceFetchError": 1}
