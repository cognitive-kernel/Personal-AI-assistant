"""Source-attributed local knowledge index using SQLite FTS5."""
from __future__ import annotations
import hashlib
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any
from .db import learning_connection

_ALLOWED_HOSTS = {
    "learn.microsoft.com", "www.w3schools.com", "microsoft.github.io",
    "docs.python.org", "fastapi.tiangolo.com", "docs.github.com",
    "developer.mozilla.org", "sqlite.org", "www.sqlite.org",
    "ollama.com", "docs.ollama.com", "pydantic.dev", "docs.pytest.org",
}
_MAX_BYTES = 2_000_000
_TIMEOUT_SECONDS = 12
_CHUNK_CHARS = 1800
_OVERLAP_CHARS = 250

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a small number of redirects, validating every target against the URL policy."""
    _MAX_REDIRECTS = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if len(getattr(req, "redirect_dict", {})) >= self._MAX_REDIRECTS:
            return None
        target = urllib.parse.urljoin(req.full_url, newurl)
        try:
            _validate_url(target)
        except ValueError:
            return None
        return super().redirect_request(req, fp, code, msg, headers, target)

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title_parts = [], []
        self._skip, self._in_title = 0, False
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}: self._skip += 1
        if tag == "title": self._in_title = True
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"}: self.parts.append("\n")
    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"} and self._skip: self._skip -= 1
        if tag == "title": self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"}: self.parts.append("\n")
    def handle_data(self, data):
        if self._in_title: self.title_parts.append(data)
        if not self._skip:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned: self.parts.append(cleaned + " ")

def _validate_url(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL نامعتبر است.") from exc
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError("فعلاً فقط HTTPS از دامنه‌های رسمی allowlist شده پشتیبانی می‌شود.")
    if parsed.username or parsed.password or port not in (None, 443):
        raise ValueError("URL نباید اطلاعات ورود یا پورت سفارشی داشته باشد.")
    if not parsed.path or parsed.path.startswith("//"):
        raise ValueError("مسیر URL معتبر نیست.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("دامنه قابل resolve نیست.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("دامنه به نشانی شبکه عمومی resolve نمی‌شود.")
    return parsed

def _fetch(url: str) -> tuple[str, str]:
    parsed = _validate_url(url)
    opener = urllib.request.build_opener(_SafeRedirect)
    request = urllib.request.Request(urllib.parse.urlunsplit(parsed),
        headers={"User-Agent": "Personal-AI-Assistant-KnowledgeIndexer/0.1", "Accept": "text/html"}, method="GET")
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            if response.status != 200: raise ValueError(f"منبع HTTP {response.status} برگرداند.")
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("فعلاً فقط صفحات HTML پشتیبانی می‌شوند.")
            raw = response.read(_MAX_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("redirect به مقصد غیرمجاز یا بیش از ۳ مرحله بود؛ مقصد باید HTTPS و در فهرست دامنه‌های مجاز باشد.") from exc
        raise OSError(f"HTTP {exc.code}") from exc
    if len(raw) > _MAX_BYTES: raise ValueError("حجم صفحه از سقف ۲ مگابایت بیشتر است.")
    try: page = raw.decode(charset, errors="replace")
    except LookupError: page = raw.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(page)
    text = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", "".join(parser.parts))).strip()
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    if len(text) < 200: raise ValueError("متن قابل استفاده کافی از صفحه استخراج نشد.")
    return title or parsed.path.rsplit("/", 1)[-1] or parsed.hostname, text

def _chunks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + _CHUNK_CHARS)
        if end < len(text):
            boundary = text.rfind(". ", start + _CHUNK_CHARS // 2, end)
            if boundary > start: end = boundary + 1
        chunk = text[start:end].strip()
        if chunk: chunks.append(chunk)
        if end >= len(text): break
        start = max(start + 1, end - _OVERLAP_CHARS)
    return chunks

def ingest_url(url: str, title: str | None = None) -> dict[str, Any]:
    parsed = _validate_url(url)
    canonical_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    fetched_title, content = _fetch(canonical_url)
    title = (title or fetched_title).strip()[:300]
    digest, chunks = hashlib.sha256(content.encode("utf-8")).hexdigest(), _chunks(content)
    license_note = "Reuse terms not independently reviewed; retain source attribution."
    with learning_connection() as conn:
        existing = conn.execute("SELECT id, content_hash FROM knowledge_sources WHERE url=?", (canonical_url,)).fetchone()
        if existing and existing["content_hash"] == digest:
            count = conn.execute("SELECT COUNT(*) AS n FROM knowledge_chunks WHERE source_id=?", (existing["id"],)).fetchone()["n"]
            return {"source_id": existing["id"], "url": canonical_url, "title": title, "chunks": count, "unchanged": True, "status": "indexed"}
        if existing:
            source_id = existing["id"]
            conn.execute("DELETE FROM knowledge_chunks_fts WHERE rowid IN (SELECT id FROM knowledge_chunks WHERE source_id=?)", (source_id,))
            conn.execute("DELETE FROM knowledge_chunks WHERE source_id=?", (source_id,))
            conn.execute("UPDATE knowledge_sources SET title=?, content_hash=?, status='indexed', fetched_at=CURRENT_TIMESTAMP, license_note=? WHERE id=?",
                         (title, digest, license_note, source_id))
        else:
            cur = conn.execute("INSERT INTO knowledge_sources(url, title, content_hash, license_note) VALUES (?, ?, ?, ?)",
                               (canonical_url, title, digest, license_note))
            source_id = int(cur.lastrowid)
        for index, chunk in enumerate(chunks):
            cur = conn.execute("INSERT INTO knowledge_chunks(source_id, chunk_index, content) VALUES (?, ?, ?)", (source_id, index, chunk))
            conn.execute("INSERT INTO knowledge_chunks_fts(rowid, content) VALUES (?, ?)", (cur.lastrowid, chunk))
    return {"source_id": source_id, "url": canonical_url, "title": title, "chunks": len(chunks),
            "unchanged": False, "status": "indexed", "license_note": license_note}

def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    terms = re.findall(r"[\w-]{2,}", query.casefold(), flags=re.UNICODE)[:12]
    if not terms: return []
    fts_query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    with learning_connection() as conn:
        rows = conn.execute("""SELECT c.id, c.content, s.id AS source_id, s.title, s.url
            FROM knowledge_chunks_fts f JOIN knowledge_chunks c ON c.id=f.rowid
            JOIN knowledge_sources s ON s.id=c.source_id
            WHERE knowledge_chunks_fts MATCH ? ORDER BY bm25(knowledge_chunks_fts) LIMIT ?""",
            (fts_query, max(1, min(20, int(limit))))).fetchall()
    return [{"chunk_id": r["id"], "source_id": r["source_id"], "title": r["title"], "url": r["url"],
             "content": r["content"], "retrieval": "sqlite_fts5"} for r in rows]

def list_sources() -> list[dict[str, Any]]:
    with learning_connection() as conn:
        rows = conn.execute("""SELECT s.id, s.url, s.title, s.content_hash, s.license_note, s.status,
            s.fetched_at, COUNT(c.id) AS chunk_count FROM knowledge_sources s
            LEFT JOIN knowledge_chunks c ON c.source_id=s.id GROUP BY s.id ORDER BY s.fetched_at DESC, s.id DESC""").fetchall()
    return [dict(row) for row in rows]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Search provider redirects are not followed.")


class _SearchLinkExtractor(HTMLParser):
    """Extract result links from the DuckDuckGo HTML results page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self._anchor_href: str | None = None
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            attrs_map = dict(attrs)
            classes = attrs_map.get("class", "")
            if "result__a" in classes.split():
                self._anchor_href = attrs_map.get("href")
    def handle_endtag(self, tag):
        if tag.lower() == "a":
            self._anchor_href = None
    def handle_data(self, data):
        if self._anchor_href:
            self.links.append(self._anchor_href)


def _search_web(query: str, limit: int) -> list[str]:
    """Search the web, but return only URLs on the existing trusted-source allowlist."""
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return []
    search_host = "html.duckduckgo.com"
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(search_host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            return []
    except OSError:
        return []
    endpoint = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query[:500]})
    request = urllib.request.Request(endpoint, headers={
        "User-Agent": "Personal-AI-Assistant/0.1 (source discovery)",
        "Accept": "text/html",
    }, method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=8) as response:
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                return []
            raw = response.read(1_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
        page = raw.decode(charset, errors="replace")
    except (OSError, ValueError, urllib.error.URLError, LookupError):
        return []
    parser = _SearchLinkExtractor()
    parser.feed(page)
    accepted: list[str] = []
    for href in parser.links:
        candidate = urllib.parse.urljoin(endpoint, href)
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.hostname == search_host:
            wrapped = urllib.parse.parse_qs(parsed.query).get("uddg", [])
            if not wrapped:
                continue
            candidate = urllib.parse.unquote(wrapped[0])
        try:
            validated = _validate_url(candidate)
        except ValueError:
            continue
        canonical = urllib.parse.urlunsplit((validated.scheme, validated.netloc, validated.path, validated.query, ""))
        if canonical not in accepted:
            accepted.append(canonical)
        if len(accepted) >= max(1, min(5, limit)):
            break
    return accepted


def research_and_index(query: str, limit: int = 3) -> dict[str, Any]:
    """Discover vetted official documentation and ingest it into the local source index."""
    urls = _search_web(query, limit=max(1, min(5, int(limit))))
    indexed, errors = [], []
    for url in urls:
        try:
            indexed.append(ingest_url(url))
        except (OSError, TimeoutError, ValueError) as exc:
            errors.append({"url": url, "error": str(exc)[:300]})
    return {
        "query": query,
        "provider": "DuckDuckGo HTML",
        "allowed_hosts": sorted(_ALLOWED_HOSTS),
        "discovered_urls": urls,
        "indexed": indexed,
        "errors": errors,
    }
