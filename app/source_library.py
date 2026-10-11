from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from .db import learning_connection

# Deliberately small allowlist for the first safe iteration. Add sources only after
# checking reliability, terms of use, and whether automated retrieval is permitted.
ALLOWED_HOSTS = {"learn.microsoft.com", "www.w3schools.com", "microsoft.github.io"}
MAX_RESPONSE_BYTES = 1_000_000
MAX_DOCUMENT_CHARS = 100_000
CHUNK_CHARS = 1800
CHUNK_OVERLAP = 250
USER_AGENT = "PersonalAI-LearningBot/0.1 (+local educational retrieval)"


class SourceError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header"}
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data.strip())
        if not self.skip_depth:
            cleaned = re.sub(r"\s+", " ", data).strip()
            if cleaned:
                self.parts.append(cleaned + " ")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(url: str) -> urllib.parse.ParseResult:
    if not isinstance(url, str) or len(url) > 2048:
        raise SourceError("URL نامعتبر یا بیش از حد طولانی است.")
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise SourceError("فقط HTTPS روی دامنه‌های آموزشی مجاز پشتیبانی می‌شود.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            raise SourceError("مقصد URL آدرس عمومی معتبر ندارد.")
    except OSError as exc:
        raise SourceError("دامنه منبع قابل resolve نیست.") from exc
    return parsed


def _fetch(url: str) -> tuple[str, str]:
    parsed = _validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"})
    opener = urllib.request.build_opener(_SafeRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            final_url = response.geturl()
            _validate_url(final_url)
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise SourceError("نوع محتوای منبع پشتیبانی نمی‌شود؛ فقط HTML یا متن ساده مجاز است.")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SourceError("حجم صفحه از سقف مجاز بیشتر است.")
            charset = response.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"دریافت منبع ناموفق بود: {str(exc)[:200]}") from exc
    decoded = body.decode(charset, errors="replace")
    if content_type == "text/plain":
        title = parsed.path.rsplit("/", 1)[-1] or parsed.hostname or url
        text = decoded
    else:
        parser = _TextExtractor()
        parser.feed(decoded)
        title = " ".join(part for part in parser.title_parts if part) or parsed.path.rsplit("/", 1)[-1] or parsed.hostname or url
        text = "".join(parser.parts)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) < 200:
        raise SourceError("متن آموزشی کافی از صفحه استخراج نشد.")
    return title[:300], text[:MAX_DOCUMENT_CHARS]


def _ensure_schema() -> None:
    with learning_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            terms_note TEXT NOT NULL DEFAULT 'مجوز بازنشر کامل بررسی نشده؛ فقط برای بازیابی محلی و همراه با ارجاع استفاده شود.'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS learning_source_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES learning_sources(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            UNIQUE(source_id, chunk_index)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_source_chunks_source ON learning_source_chunks(source_id, chunk_index)")


def _chunks(text: str) -> list[str]:
    # Fixed-size sliding windows guarantee a hard size cap and retain context
    # across boundaries. Prefer a nearby word boundary when possible.
    normalized = re.sub(r"\\s+", " ", text).strip()
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + CHUNK_CHARS)
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks

def ingest_source(url: str) -> dict[str, Any]:
    _ensure_schema()
    # Validate before any network request, including DNS resolution.
    _validate_url(url)
    title, content = _fetch(url)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    fetched_at = datetime.now(timezone.utc).isoformat()
    with learning_connection() as conn:
        conn.execute("""INSERT INTO learning_sources(url, title, content, content_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET title=excluded.title, content=excluded.content,
            content_hash=excluded.content_hash, fetched_at=excluded.fetched_at""",
            (url, title, content, digest, fetched_at))
        source_id = int(conn.execute("SELECT id FROM learning_sources WHERE url=?", (url,)).fetchone()["id"])
        conn.execute("DELETE FROM learning_source_chunks WHERE source_id=?", (source_id,))
        conn.executemany("INSERT INTO learning_source_chunks(source_id, chunk_index, content) VALUES (?, ?, ?)",
                         [(source_id, i, chunk) for i, chunk in enumerate(_chunks(content))])
    return {"url": url, "title": title, "content_hash": digest, "character_count": len(content),
            "chunk_count": len(_chunks(content)), "fetched_at": fetched_at,
            "terms_note": "مجوز بازنشر کامل بررسی نشده؛ متن برای بازیابی محلی نگهداری می‌شود و باید ارجاع داده شود."}


def list_sources() -> list[dict[str, Any]]:
    _ensure_schema()
    with learning_connection() as conn:
        rows = conn.execute("""SELECT id, url, title, content_hash, fetched_at, terms_note,
            length(content) AS character_count,
            (SELECT COUNT(*) FROM learning_source_chunks c WHERE c.source_id=s.id) AS chunk_count
            FROM learning_sources s ORDER BY fetched_at DESC""").fetchall()
    return [dict(row) for row in rows]


def _terms(value: str) -> set[str]:
    value = value.casefold().replace("ي", "ی").replace("ك", "ک")
    return {token for token in re.findall(r"[\w+#.-]+", value, flags=re.UNICODE) if len(token) > 1}


def retrieve(topic: str, limit: int = 4) -> list[dict[str, Any]]:
    """Small lexical baseline; designed so a vector index can replace it later."""
    _ensure_schema()
    terms = _terms(topic)
    if not terms:
        return []
    with learning_connection() as conn:
        rows = conn.execute("""SELECT c.content, s.url, s.title, s.content_hash, s.fetched_at
            FROM learning_source_chunks c JOIN learning_sources s ON s.id=c.source_id""").fetchall()
    ranked = []
    for row in rows:
        chunk_terms = _terms(row["content"])
        overlap = len(terms & chunk_terms)
        if not overlap:
            continue
        score = overlap / (len(terms) ** 0.5) + sum(0.15 for term in terms if term in row["content"].casefold())
        ranked.append((score, dict(row)))
    ranked.sort(key=lambda item: (item[0], item[1]["fetched_at"]), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    for score, item in ranked:
        fingerprint = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
        if fingerprint in seen_chunks:
            continue
        item["retrieval_score"] = round(score, 3)
        selected.append(item)
        seen_chunks.add(fingerprint)
        if len(selected) >= max(1, min(8, limit)):
            break
    return selected
