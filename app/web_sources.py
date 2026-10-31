"""Bounded multi-engine web search and public-source retrieval.

Search results from DuckDuckGo, Bing, and Google are normalized and deduplicated.
All page fetches reject non-public destinations to reduce SSRF risk.
"""
from __future__ import annotations

import base64
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request

from .pinned_http import PinnedHTTPHandler, PinnedHTTPSHandler


SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_LITE_SEARCH_ENDPOINT = "https://lite.duckduckgo.com/lite/"
BING_SEARCH_ENDPOINT = "https://www.bing.com/search"
GOOGLE_SEARCH_ENDPOINT = "https://www.google.com/search"
SEARCH_PROVIDER_HOSTS = ("duckduckgo.com", "bing.com", "google.com")
_TRACKING_QUERY_PARAMS = frozenset({
    "gclid", "dclid", "fbclid", "msclkid", "gbraid", "wbraid",
    "ved", "usg", "ei", "sa", "sclient", "oq", "opi", "sca_esv",
    "iflsig", "gws_rd", "uact", "ref_src",
})
# The general sandbox/container query is prone to search-engine no-result or
# challenge pages. Try short, bounded synonyms before giving up on that topic.
SEARCH_QUERY_ALTERNATES = {
    "official free developer sandbox container compute limits": (
        "free cloud development environment sandbox runtime limits official",
        "developer container sandbox free quota usage terms",
    ),
}
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 6
# Kaggle's current Terms of Use prohibit automated crawling/scraping of its services.
# Use its documented CLI/API through an explicitly authorized adapter instead.
AUTOMATED_FETCH_BLOCKED_HOSTS = ("kaggle.com",)


class SourceFetchError(RuntimeError):
    """Raised when a web source cannot be fetched safely."""


@dataclass(frozen=True)
class SourceDocument:
    url: str
    title: str
    text: str


class SearchResultList(list[tuple[str, str]]):
    """Search results with bounded diagnostics for each engine attempted."""

    def __init__(
        self,
        values=(),
        *,
        provider_diagnostics: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(values)
        self.provider_diagnostics = provider_diagnostics or []


class SourceDocumentList(list[SourceDocument]):
    """Fetched pages plus bounded diagnostics without retaining query or page content."""

    def __init__(
        self,
        values=(),
        *,
        search_provider_diagnostics: list[dict[str, object]] | None = None,
        fetch_diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(values)
        self.search_provider_diagnostics = search_provider_diagnostics or []
        self.fetch_diagnostics = fetch_diagnostics or {}


def _parse_public_url(url: str) -> urllib.parse.ParseResult:
    """Validate URL syntax and destination policy without performing DNS I/O."""
    if not isinstance(url, str) or len(url) > 2048:
        raise SourceFetchError("URL is invalid or too long.")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceFetchError("Only absolute HTTP(S) URLs are supported.")
    if parsed.username or parsed.password:
        raise SourceFetchError("URLs containing credentials are not allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceFetchError("URL port is invalid.") from exc
    if port not in {None, 80, 443}:
        raise SourceFetchError("Non-standard ports are not allowed.")
    hostname = parsed.hostname.rstrip(".").lower()
    if any(
        hostname == blocked or hostname.endswith("." + blocked)
        for blocked in AUTOMATED_FETCH_BLOCKED_HOSTS
    ):
        raise SourceFetchError(
            "Automated page fetching is disabled for this provider; use its documented API/CLI if authorized."
        )
    if hostname == "localhost":
        raise SourceFetchError("Local network destinations are not allowed.")
    return parsed


def _validated_public_addresses(parsed: urllib.parse.ParseResult) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    hostname = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            addresses = [
                ipaddress.ip_address(record[4][0].split("%", 1)[0])
                for record in records
            ]
        except (OSError, ValueError, IndexError, TypeError) as exc:
            raise SourceFetchError("URL host could not be resolved safely.") from exc
    else:
        addresses = [literal]
    if not addresses or any(not address.is_global for address in addresses):
        raise SourceFetchError("Local, private, or non-public network destinations are blocked.")
    return addresses


def _validate_public_url(url: str) -> urllib.parse.ParseResult:
    parsed = _parse_public_url(url)
    _validated_public_addresses(parsed)
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # DNS policy is enforced by the pinned transport at connection time.
        # Parsing here prevents redirects from escaping the URL policy without
        # performing a second, redundant DNS lookup.
        _parse_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _resolve_public_ip(url: str) -> str:
    """Resolve once, validate every DNS answer, and pin the selected socket IP."""
    parsed = _parse_public_url(url)
    return _validated_public_addresses(parsed)[0].compressed


def _open(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_bytes: int = MAX_RESPONSE_BYTES):
    _parse_public_url(url)
    # Ignore environment proxy settings so a proxy cannot silently change the
    # destination boundary enforced above.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SafeRedirectHandler(),
        PinnedHTTPHandler(_resolve_public_ip),
        PinnedHTTPSHandler(_resolve_public_ip),
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Personal-AI-Assistant/0.1 (learning source discovery)", "Accept": "text/html,text/plain;q=0.9"},
    )
    try:
        response = opener.open(request, timeout=timeout)
        with response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise SourceFetchError(f"Unsupported source content type: {content_type}")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise SourceFetchError("Source response exceeded the configured size limit.")
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace"), content_type
    except SourceFetchError:
        raise
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise SourceFetchError(f"Source request failed: {exc}") from exc


class _SearchResultParser(HTMLParser):
    """Parse DuckDuckGo HTML result links, including its redirect wrapper."""

    def __init__(self, base_url: str = SEARCH_ENDPOINT) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.results: list[tuple[str, str]] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        classes = values.get("class", "").split()
        if ({"result__a", "result-link"} & set(classes)) and values.get("href"):
            self._active_href = values["href"]
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_href is not None:
            title = " ".join("".join(self._active_text).split())
            href = urllib.parse.urljoin(self.base_url, self._active_href)
            parsed = urllib.parse.urlparse(href)
            if parsed.hostname and parsed.hostname.lower().endswith("duckduckgo.com"):
                redirected = urllib.parse.parse_qs(parsed.query).get("uddg", [])
                if redirected:
                    href = urllib.parse.unquote(redirected[0])
            if title and href:
                self.results.append((title, href))
            self._active_href = None
            self._active_text = []


class _BingSearchResultParser(HTMLParser):
    """Extract only title links from Bing's organic-result containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str]] = []
        self._in_result = False
        self._result_tag: str | None = None
        self._in_heading = False
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        classes = values.get("class", "").split()
        if tag == "li" and "b_algo" in classes:
            self._in_result = True
            self._result_tag = tag
        elif self._in_result and tag == "h2":
            self._in_heading = True
        elif self._in_result and self._in_heading and tag == "a" and values.get("href"):
            self._active_href = values["href"]
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_href is not None:
            title = " ".join("".join(self._active_text).split())
            href = self._active_href
            # Bing commonly wraps organic destinations in /ck/a links. Its
            # `u=a1...` parameter contains a URL-safe base64 destination;
            # normalize that destination before public-URL validation, or the
            # search provider's own hostname will be rejected as a result.
            parsed_href = urllib.parse.urlparse(href)
            if (
                parsed_href.hostname
                and parsed_href.hostname.lower().rstrip(".") in {"bing.com", "www.bing.com"}
                and parsed_href.path.rstrip("/") == "/ck/a"
            ):
                encoded = urllib.parse.parse_qs(parsed_href.query).get("u", [""])[0]
                if encoded.startswith("a1") and len(encoded) > 2:
                    try:
                        payload = encoded[2:]
                        payload += "=" * (-len(payload) % 4)
                        decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
                        decoded_url = urllib.parse.urlparse(decoded)
                        if decoded_url.scheme in {"http", "https"} and decoded_url.hostname:
                            href = decoded
                    except (ValueError, UnicodeDecodeError):
                        pass
            if title:
                self.results.append((title, href))
            self._active_href = None
            self._active_text = []
        elif tag == "h2":
            self._in_heading = False
        elif tag == self._result_tag and self._in_result:
            self._in_result = False
            self._result_tag = None


class _GoogleSearchResultParser(HTMLParser):
    """Extract result links from Google classic and standard HTML markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str]] = []
        self._active_href: str | None = None
        self._in_heading = 0
        self._heading_text: list[str] = []
        self._active_title_attrs: dict[str, str] = {}
        self._heading_tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href") and self._active_href is None:
            self._active_href = values["href"]
            self._heading_text = []
            self._active_title_attrs = {
                key: values[key] for key in ("aria-label", "title") if values.get(key)
            }
        elif tag == "h3" and self._active_href is not None:
            self._in_heading += 1
            self._heading_tag_stack.append("h3")
        elif tag == "div" and self._active_href is not None and values.get("role") == "heading":
            self._in_heading += 1
            self._heading_tag_stack.append("div")

    def handle_data(self, data: str) -> None:
        if self._active_href is not None and self._in_heading:
            self._heading_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._heading_tag_stack and tag == self._heading_tag_stack[-1]:
            self._heading_tag_stack.pop()
            self._in_heading = max(0, self._in_heading - 1)
        elif tag == "a" and self._active_href is not None:
            heading_title = " ".join("".join(self._heading_text).split())
            title = heading_title or self._active_title_attrs.get("aria-label", "").strip()
            title = title or self._active_title_attrs.get("title", "").strip()
            href = urllib.parse.urljoin(GOOGLE_SEARCH_ENDPOINT, self._active_href)
            parsed = urllib.parse.urlparse(href)
            if (
                parsed.hostname
                and (
                    parsed.hostname.casefold().rstrip(".") == "google.com"
                    or parsed.hostname.casefold().rstrip(".").endswith(".google.com")
                )
                and parsed.path.rstrip("/") == "/url"
            ):
                params = urllib.parse.parse_qs(parsed.query)
                destination = (params.get("q") or params.get("url") or [""])[0]
                if destination:
                    # parse_qs has already decoded the wrapper's outer encoding.
                    # A second unquote could corrupt percent-encoded target queries.
                    href = destination
            if title and href:
                self.results.append((title, href))
            self._active_href = None
            self._heading_text = []
            self._active_title_attrs = {}
            self._heading_tag_stack = []
            self._in_heading = 0


class _PageTextParser(HTMLParser):
    _IGNORED = {"script", "style", "svg", "nav", "footer", "header", "form", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._IGNORED:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in {"p", "div", "li", "h1", "h2", "h3", "br"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._ignored_depth:
            cleaned = " ".join(data.split())
            if cleaned:
                if self.text_parts and not self.text_parts[-1].endswith("\n"):
                    self.text_parts.append(" ")
                self.text_parts.append(cleaned)


def _clean_search_result_url(url: str) -> str:
    """Drop fragments/tracking parameters and normalize harmless URL variation."""
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    port = parsed.port
    netloc = hostname
    if port is not None and not (
        (parsed.scheme.casefold() == "https" and port == 443)
        or (parsed.scheme.casefold() == "http" and port == 80)
    ):
        netloc = f"{hostname}:{port}"
    query_items = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_PARAMS
        and not key.casefold().startswith("utm_")
    ]
    query_items.sort()
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "/",
            urllib.parse.urlencode(query_items, doseq=True),
            "",
        )
    )


def result_url_identity(url: str) -> str:
    """Return a stable identity used to suppress duplicate results across engines/runs."""
    cleaned = _clean_search_result_url(url)
    parsed = urllib.parse.urlsplit(cleaned)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{hostname}:{parsed.port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path or "/", parsed.query, "")
    )


def _normalize_search_results(
    results: list[tuple[str, str]], *, limit: int, provider_hosts: tuple[str, ...]
) -> list[tuple[str, str]]:
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, url in results:
        try:
            parsed = _validate_public_url(url)
        except SourceFetchError:
            continue
        # Research payloads, audit records, and citations require HTTPS. Reject
        # HTTP search results here so they cannot consume the bounded result limit
        # and later be discarded by chat_research._source_payload.
        if parsed.scheme.casefold() != "https":
            continue
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if any(hostname == host or hostname.endswith("." + host) for host in provider_hosts):
            continue
        normalized = _clean_search_result_url(urllib.parse.urlunparse(parsed._replace(fragment="")))
        identity = result_url_identity(normalized)
        if title.strip() and identity not in seen:
            seen.add(identity)
            unique.append((" ".join(title.split())[:300], normalized))
        if len(unique) >= max(1, min(10, limit)):
            break
    return unique


def _search_page_failure_reason(html: str, provider: str) -> str:
    """Classify common consent/challenge pages without attempting to bypass them."""
    normalized = html.casefold()
    if provider == "Google" and any(
        marker in normalized
        for marker in (
            "our systems have detected unusual traffic",
            "before you continue to google",
            "consent.google.com",
            "/sorry/",
            "recaptcha",
        )
    ):
        return "challenge_or_consent_page"
    if provider == "DuckDuckGo" and any(
        marker in normalized
        for marker in (
            "verify you are human",
            "automated requests",
            "captcha",
            "anomaly-modal",
        )
    ):
        return "challenge_page"
    return "no_parseable_results"


def _search_results(query: str, limit: int) -> SearchResultList:
    """Collect deduplicated results fairly, using one bounded alternate endpoint per engine."""
    providers = (
        (
            "DuckDuckGo",
            (
                ("html", SEARCH_ENDPOINT, {}),
                ("lite", DUCKDUCKGO_LITE_SEARCH_ENDPOINT, {"kl": "us-en"}),
            ),
            _SearchResultParser,
            ("duckduckgo.com",),
        ),
        (
            "Bing",
            (("standard", BING_SEARCH_ENDPOINT, {}),),
            _BingSearchResultParser,
            ("bing.com",),
        ),
        (
            "Google",
            (
                ("standard", GOOGLE_SEARCH_ENDPOINT, {}),
                ("basic_html", GOOGLE_SEARCH_ENDPOINT, {"gbv": "1", "hl": "en", "num": "10"}),
            ),
            _GoogleSearchResultParser,
            ("google.com",),
        ),
    )
    provider_diagnostics: list[dict[str, object]] = [
        {
            "provider": name,
            "attempts": [],
            "parsed_result_count": 0,
            "accepted_result_count": 0,
            "unique_result_count": 0,
            "duplicate_result_count": 0,
        }
        for name, _, _, _ in providers
    ]
    failures: list[str] = []
    max_results = max(1, min(30, limit * len(providers) * 3))
    query_variants = (query,) + SEARCH_QUERY_ALTERNATES.get(query.strip().casefold(), ())
    for variant_index, search_query in enumerate(query_variants):
        variant_label = "primary" if variant_index == 0 else f"alternate_{variant_index}"
        results_by_provider: list[list[tuple[str, str]]] = [[] for _ in providers]
        successful_attempt_by_provider: list[dict[str, object] | None] = [None] * len(providers)
        for provider_index, (name, endpoints, parser_type, provider_hosts) in enumerate(providers):
            stats = provider_diagnostics[provider_index]
            attempts = stats["attempts"]
            assert isinstance(attempts, list)
            # Use the secondary public HTML endpoint only when the primary yields
            # no parseable organic results. Do not attempt to solve CAPTCHAs or consent walls.
            for endpoint_variant, endpoint, extra_params in endpoints:
                attempt: dict[str, object] = {
                    "variant": variant_label,
                    "endpoint_variant": endpoint_variant,
                    "parsed_result_count": 0,
                    "accepted_result_count": 0,
                    "unique_result_count": 0,
                    "duplicate_result_count": 0,
                    "error": None,
                }
                attempts.append(attempt)
                search_url = endpoint + "?" + urllib.parse.urlencode(
                    {"q": search_query, **extra_params}
                )
                try:
                    html, _ = _open(search_url, max_bytes=MAX_RESPONSE_BYTES)
                    parser = parser_type(base_url=endpoint) if parser_type is _SearchResultParser else parser_type()
                    parser.feed(html)
                    parsed_count = len(parser.results)
                    attempt["parsed_result_count"] = parsed_count
                    stats["parsed_result_count"] = int(stats["parsed_result_count"]) + parsed_count
                    results = _normalize_search_results(
                        parser.results, limit=limit, provider_hosts=provider_hosts
                    )
                    accepted_count = len(results)
                    attempt["accepted_result_count"] = accepted_count
                    stats["accepted_result_count"] = int(stats["accepted_result_count"]) + accepted_count
                    if not results:
                        reason = _search_page_failure_reason(html, name)
                        attempt["error"] = reason
                        failures.append(f"{name}:{reason}[{variant_label}:{endpoint_variant}]")
                        continue
                    results_by_provider[provider_index] = results
                    successful_attempt_by_provider[provider_index] = attempt
                    break
                except SourceFetchError as exc:
                    attempt["error"] = type(exc).__name__
                    failures.append(f"{name}:{type(exc).__name__}[{variant_label}:{endpoint_variant}]")

        # Round-robin results so a full result set from one engine cannot crowd out
        # unique links from other engines before public source pages are fetched.
        combined: list[tuple[str, str]] = []
        seen: set[str] = set()
        max_provider_results = max((len(items) for items in results_by_provider), default=0)
        for result_index in range(max_provider_results):
            for provider_index, provider_results in enumerate(results_by_provider):
                if result_index >= len(provider_results):
                    continue
                title, url = provider_results[result_index]
                identity = result_url_identity(url)
                stats = provider_diagnostics[provider_index]
                attempt = successful_attempt_by_provider[provider_index]
                if attempt is None:
                    continue
                if identity in seen:
                    attempt["duplicate_result_count"] = int(attempt["duplicate_result_count"]) + 1
                    stats["duplicate_result_count"] = int(stats["duplicate_result_count"]) + 1
                    continue
                seen.add(identity)
                combined.append((title, url))
                attempt["unique_result_count"] = int(attempt["unique_result_count"]) + 1
                stats["unique_result_count"] = int(stats["unique_result_count"]) + 1
                if len(combined) >= max_results:
                    break
            if len(combined) >= max_results:
                break
        if combined:
            return SearchResultList(combined[:max_results], provider_diagnostics=provider_diagnostics)

    error = SourceFetchError(
        "All configured search providers and bounded query variants failed or returned "
        "no parseable results (" + ", ".join(failures) + ")."
    )
    error.provider_diagnostics = provider_diagnostics
    raise error

def _fetch_document(url: str, title_hint: str, *, max_chars: int = 6000) -> SourceDocument:
    parsed = _parse_public_url(url)
    if parsed.scheme.casefold() != "https":
        raise SourceFetchError("HTTPS is required for research source documents.")
    content, content_type = _open(url, max_bytes=MAX_RESPONSE_BYTES)
    if content_type == "text/plain":
        title = title_hint
        text = content
    else:
        parser = _PageTextParser()
        parser.feed(content)
        title = " ".join("".join(parser.title_parts).split()) or title_hint
        raw_text = "".join(parser.text_parts)
        # Keep paragraph/list boundaries for contextual relevance checks, while
        # normalizing spaces within each block for compact storage and search.
        raw_text = re.sub(r"[ \t\f\v]+", " ", raw_text)
        raw_text = re.sub(r" *\n *", "\n", raw_text)
        text = re.sub(r"\n{2,}", "\n", raw_text)
    text = text.strip()[:max_chars]
    if len(text) < 100:
        raise SourceFetchError("Source page did not contain enough readable text.")
    return SourceDocument(url=url, title=title[:300], text=text)


def fetch_public_document(
    url: str, title_hint: str, *, max_chars: int = 6000
) -> SourceDocument:
    """Fetch a known public HTTPS source with the same safety checks as search results."""
    if not isinstance(title_hint, str) or not title_hint.strip():
        raise SourceFetchError("A non-empty source title hint is required.")
    return _fetch_document(url, title_hint, max_chars=max_chars)


def discover_sources(query: str, *, limit: int = 3) -> list[SourceDocument]:
    """Search and fetch public pages; raise when search providers cannot return results."""
    if not isinstance(query, str) or not query.strip():
        return SourceDocumentList()
    candidates = _search_results(query.strip()[:300], max(1, min(5, limit)))
    fetch_diagnostics: dict[str, object] = {
        "candidate_count": len(candidates),
        "attempted_count": 0,
        "fetched_count": 0,
        "failure_counts": {},
    }
    documents = SourceDocumentList(
        search_provider_diagnostics=getattr(candidates, "provider_diagnostics", []),
        fetch_diagnostics=fetch_diagnostics,
    )
    for title, url in candidates:
        fetch_diagnostics["attempted_count"] = int(fetch_diagnostics["attempted_count"]) + 1
        try:
            document = _fetch_document(url, title)
        except SourceFetchError as exc:
            failures = fetch_diagnostics["failure_counts"]
            assert isinstance(failures, dict)
            error_type = type(exc).__name__
            failures[error_type] = int(failures.get(error_type, 0)) + 1
            continue
        documents.append(document)
        fetch_diagnostics["fetched_count"] = int(fetch_diagnostics["fetched_count"]) + 1
        if len(documents) >= max(1, min(5, limit)):
            break
    return documents
