"""Small, bounded web-search adapter for automatic learning-source discovery.

The first provider is DuckDuckGo's public HTML results page. All page fetches
are bounded and reject non-public destinations to reduce SSRF risk.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
import urllib.error
import urllib.parse
import urllib.request


SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 6


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
    hostname = parsed.hostname.rstrip(".")
    if hostname.lower() == "localhost":
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
        if "result__a" in classes and values.get("href"):
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


def _search_results(query: str, limit: int) -> list[tuple[str, str]]:
    search_url = SEARCH_ENDPOINT + "?" + urllib.parse.urlencode({"q": query})
    html, _ = _open(search_url, max_bytes=MAX_RESPONSE_BYTES)
    parser = _SearchResultParser()
    parser.feed(html)
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, url in parser.results:
        try:
            parsed = _validate_public_url(url)
        except SourceFetchError:
            continue
        normalized = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if normalized not in seen and parsed.hostname and not parsed.hostname.lower().endswith("duckduckgo.com"):
            seen.add(normalized)
            unique.append((title, normalized))
        if len(unique) >= max(1, min(10, limit * 3)):
            break
    return unique


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


def discover_sources(query: str, *, limit: int = 3) -> list[SourceDocument]:
    """Search and fetch a few public web pages; unavailable sources return an empty list."""
    if not isinstance(query, str) or not query.strip():
        return []
    try:
        candidates = _search_results(query.strip()[:300], max(1, min(5, limit)))
    except SourceFetchError:
        return []
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
