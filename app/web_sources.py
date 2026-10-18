"""Small, bounded web-search adapter for automatic learning-source discovery.

The first provider is DuckDuckGo's public HTML results page. All page fetches
are bounded and reject non-public destinations to reduce SSRF risk.
"""
from __future__ import annotations

import base64
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request


SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
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


def _validate_public_url(url: str) -> urllib.parse.ParseResult:
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
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            records = socket.getaddrinfo(
                hostname, port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            addresses = [ipaddress.ip_address(record[4][0].split("%", 1)[0]) for record in records]
        except (OSError, ValueError) as exc:
            raise SourceFetchError("URL host could not be resolved safely.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise SourceFetchError("Local, private, or non-public network destinations are blocked.")
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_bytes: int = MAX_RESPONSE_BYTES):
    _validate_public_url(url)
    # Ignore environment proxy settings so a proxy cannot silently change the
    # destination boundary enforced above.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SafeRedirectHandler(),
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

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
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
            href = urllib.parse.urljoin(SEARCH_ENDPOINT, self._active_href)
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
    """Extract organic Google links by anchors containing a result heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str]] = []
        self._active_href: str | None = None
        self._in_heading = 0
        self._heading_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href") and self._active_href is None:
            self._active_href = values["href"]
            self._heading_text = []
        elif tag == "h3" and self._active_href is not None:
            self._in_heading += 1

    def handle_data(self, data: str) -> None:
        if self._active_href is not None and self._in_heading:
            self._heading_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_heading:
            self._in_heading -= 1
        elif tag == "a" and self._active_href is not None:
            title = " ".join("".join(self._heading_text).split())
            href = urllib.parse.urljoin(GOOGLE_SEARCH_ENDPOINT, self._active_href)
            parsed = urllib.parse.urlparse(href)
            if (
                parsed.hostname
                and parsed.hostname.casefold().rstrip(".").endswith("google.com")
                and parsed.path.rstrip("/") == "/url"
            ):
                params = urllib.parse.parse_qs(parsed.query)
                destination = (params.get("q") or params.get("url") or [""])[0]
                if destination:
                    href = urllib.parse.unquote(destination)
            if title and href:
                self.results.append((title, href))
            self._active_href = None
            self._heading_text = []


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


def _search_results(query: str, limit: int) -> list[tuple[str, str]]:
    """Collect results across configured search engines and deduplicate source URLs."""
    providers = (
        ("DuckDuckGo", SEARCH_ENDPOINT, _SearchResultParser, ("duckduckgo.com",)),
        ("Bing", BING_SEARCH_ENDPOINT, _BingSearchResultParser, ("bing.com",)),
        ("Google", GOOGLE_SEARCH_ENDPOINT, _GoogleSearchResultParser, ("google.com",)),
    )
    failures: list[str] = []
    max_results = max(1, min(30, limit * len(providers) * 3))
    query_variants = (query,) + SEARCH_QUERY_ALTERNATES.get(query.strip().casefold(), ())
    for variant_index, search_query in enumerate(query_variants):
        variant_label = "primary" if variant_index == 0 else f"alternate_{variant_index}"
        combined: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, endpoint, parser_type, provider_hosts in providers:
            search_url = endpoint + "?" + urllib.parse.urlencode({"q": search_query})
            try:
                html, _ = _open(search_url, max_bytes=MAX_RESPONSE_BYTES)
                parser = parser_type()
                parser.feed(html)
                results = _normalize_search_results(
                    parser.results, limit=limit, provider_hosts=provider_hosts
                )
                if not results:
                    failures.append(f"{name}:no_parseable_results[{variant_label}]")
                    continue
                for title, url in results:
                    identity = result_url_identity(url)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    combined.append((title, url))
                    if len(combined) >= max_results:
                        break
            except SourceFetchError as exc:
                failures.append(f"{name}:{type(exc).__name__}[{variant_label}]")
        if combined:
            return combined[:max_results]
    raise SourceFetchError(
        "All configured search providers and bounded query variants failed or returned "
        "no parseable results ("
        + ", ".join(failures)
        + ")."
    )

def _fetch_document(url: str, title_hint: str, *, max_chars: int = 6000) -> SourceDocument:
    content, content_type = _open(url, max_bytes=MAX_RESPONSE_BYTES)
    if content_type == "text/plain":
        title = title_hint
        text = content
    else:
        parser = _PageTextParser()
        parser.feed(content)
        title = " ".join("".join(parser.title_parts).split()) or title_hint
        text = " ".join("".join(parser.text_parts).split())
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
        return []
    candidates = _search_results(query.strip()[:300], max(1, min(5, limit)))
    documents: list[SourceDocument] = []
    for title, url in candidates:
        try:
            document = _fetch_document(url, title)
        except SourceFetchError:
            continue
        documents.append(document)
        if len(documents) >= max(1, min(5, limit)):
            break
    return documents
