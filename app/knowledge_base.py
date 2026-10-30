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
from .pinned_http import PinnedHTTPSHandler

_ALLOWED_HOSTS = {"learn.microsoft.com", "www.w3schools.com", "microsoft.github.io"}
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
            # Enforce scheme/host/port policy here. DNS is validated exactly once
            # by the pinned transport immediately before the TCP connection.
            _parse_url_policy(target)
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

def _parse_url_policy(url: str) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("URL نامعتبر یا بیش از حد طولانی است.")
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
    return parsed


def _public_addresses(hostname: str) -> list[str]:
    try:
        records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("دامنه قابل resolve نیست.") from exc
    addresses = sorted({record[4][0] for record in records})
    try:
        safe = bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError as exc:
        raise ValueError("DNS نشانی IP معتبر برنگرداند.") from exc
    if not safe:
        raise ValueError("دامنه به نشانی شبکه عمومی resolve نمی‌شود.")
    return addresses


def _validate_url(url: str) -> urllib.parse.SplitResult:
    parsed = _parse_url_policy(url)
    _public_addresses(parsed.hostname or "")
    return parsed


def _resolve_pinned_ip(url: str) -> str:
    # Resolve once per actual request and pin the connection to the validated IP.
    parsed = _parse_url_policy(url)
    return _public_addresses(parsed.hostname or "")[0]

def _fetch(url: str) -> tuple[str, str]:
    # Parse policy without resolving DNS; the transport validates every DNS
    # answer and pins the selected address immediately before connecting.
    parsed = _parse_url_policy(url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SafeRedirect,
        PinnedHTTPSHandler(_resolve_pinned_ip),
    )
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
    # Network policy is enforced again at the pinned transport boundary; avoid
    # an unnecessary DNS lookup before the actual request.
    parsed = _parse_url_policy(url)
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
