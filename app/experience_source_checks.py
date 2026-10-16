"""Explicit, bounded revalidation of saved experience source evidence.

This module records content fingerprints and outcomes only. It never mutates or
re-verifies the experience itself; a changed source requires human review.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

from . import experience, web_sources
from .db import init_databases, learning_connection

MAX_SOURCES_PER_REFRESH = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalized_source_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Saved source URL is invalid.")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Only saved HTTPS source URLs can be revalidated.")
    if parsed.username or parsed.password:
        raise ValueError("Saved source URLs containing credentials are blocked.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Saved source URL has an invalid port.") from exc
    return urlunparse(parsed._replace(fragment=""))


def _latest_hash(experience_id: int, source_url: str) -> str | None:
    with learning_connection() as connection:
        row = connection.execute(
            """SELECT content_sha256 FROM learning_experience_source_checks
               WHERE experience_id=? AND source_url=? AND content_sha256 IS NOT NULL
               ORDER BY checked_at DESC, id DESC LIMIT 1""",
            (experience_id, source_url),
        ).fetchone()
    return str(row["content_sha256"]) if row and row["content_sha256"] else None


def _record(
    *,
    experience_id: int,
    version: int,
    source_url: str,
    outcome: str,
    content_sha256: str | None = None,
    previous_content_sha256: str | None = None,
    title: str = "",
    error_kind: str = "",
    checked_at: str,
) -> dict[str, Any]:
    init_databases()
    with learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO learning_experience_source_checks(
                   experience_id, experience_version, source_url, outcome,
                   content_sha256, previous_content_sha256, title, error_kind, checked_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                experience_id, version, source_url, outcome, content_sha256,
                previous_content_sha256, title[:300], error_kind[:80], checked_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM learning_experience_source_checks WHERE id=?",
            (int(cursor.lastrowid),),
        ).fetchone()
    return dict(row)


def refresh_experience_sources(experience_id: int) -> dict[str, Any]:
    """Re-fetch up to three saved HTTPS sources and persist a bounded audit result."""
    item = experience.get_experience(experience_id)
    evidence = item.get("source_evidence", [])
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("Experience has no saved source evidence to revalidate.")

    urls: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        raw_url = entry.get("url")
        if not isinstance(raw_url, str):
            continue
        try:
            url = _normalized_source_url(raw_url)
        except ValueError:
            # Keep malformed saved URLs visible as blocked results, but do not fetch them.
            url = raw_url[:2048]
        if url in seen:
            continue
        seen.add(url)
        urls.append((url, str(entry.get("claim", ""))[:300]))
        if len(urls) >= MAX_SOURCES_PER_REFRESH:
            break
    if not urls:
        raise ValueError("Experience has no revalidatable source URLs.")

    checked_at = _now()
    results: list[dict[str, Any]] = []
    for url, title_hint in urls:
        try:
            normalized = _normalized_source_url(url)
            web_sources._validate_public_url(normalized)
        except (ValueError, web_sources.SourceFetchError):
            results.append(_record(
                experience_id=experience_id,
                version=int(item.get("version", 1)),
                source_url=url,
                outcome="blocked",
                error_kind="SourcePolicyRejected",
                checked_at=checked_at,
            ))
            continue

        previous_hash = _latest_hash(experience_id, normalized)
        try:
            document = web_sources._fetch_document(normalized, title_hint, max_chars=6000)
            fingerprint = hashlib.sha256(
                (document.title.strip() + "\n" + " ".join(document.text.split())).encode("utf-8")
            ).hexdigest()
            if previous_hash is None:
                outcome = "baseline"
            elif previous_hash == fingerprint:
                outcome = "unchanged"
            else:
                outcome = "changed"
            results.append(_record(
                experience_id=experience_id,
                version=int(item.get("version", 1)),
                source_url=normalized,
                outcome=outcome,
                content_sha256=fingerprint,
                previous_content_sha256=previous_hash,
                title=document.title,
                checked_at=checked_at,
            ))
        except web_sources.SourceFetchError:
            results.append(_record(
                experience_id=experience_id,
                version=int(item.get("version", 1)),
                source_url=normalized,
                outcome="unavailable",
                previous_content_sha256=previous_hash,
                error_kind="SourceFetchError",
                checked_at=checked_at,
            ))

    counts: dict[str, int] = {}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    return {
        "experience_id": experience_id,
        "experience_version": int(item.get("version", 1)),
        "checked_at": checked_at,
        "checks": results,
        "counts": counts,
        "experience_mutated": False,
        "verified": False,
        "note": "Content fingerprints cover bounded extracted text, not the entire page. Changed or unavailable sources require manual review.",
    }


def list_source_checks(experience_id: int, limit: int = 30) -> list[dict[str, Any]]:
    experience.get_experience(experience_id)  # Validate the parent record exists.
    init_databases()
    with learning_connection() as connection:
        rows = connection.execute(
            """SELECT id, experience_id, experience_version, source_url, outcome,
                      content_sha256, previous_content_sha256, title, error_kind, checked_at
               FROM learning_experience_source_checks
               WHERE experience_id=? ORDER BY checked_at DESC, id DESC LIMIT ?""",
            (experience_id, max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]
