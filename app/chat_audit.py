"""Privacy-preserving audit metadata for research-aware chat runs.

Raw questions, answers, and source excerpts are deliberately not persisted here.
Only hashes, source provenance metadata, review summaries, memory references, and
timing are stored in the local learning database.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import learning_connection

_ALLOWED_REVIEW_STATUSES = {
    "no_sources",
    "passed_model_assisted",
    "passed_model_assisted_after_repair",
    "needs_review",
    "review_unavailable",
    "generation_failed",
}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_metadata(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source in sources[:3]:
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        content = source.get("content")
        title = source.get("title", "")
        content = content if isinstance(content, str) else ""
        result.append({
            "url": url[:1000],
            "title": str(title)[:250],
            "content_sha256": _sha256(content),
            "excerpt_chars": len(content),
        })
    return result


def _source_discovery_summary(value: Any) -> dict[str, Any] | None:
    """Keep only bounded, non-identifying search/fetch counters and known error labels."""
    if not isinstance(value, dict):
        return None
    provider_rows = value.get("providers", [])
    providers = []
    if isinstance(provider_rows, list):
        for item in provider_rows[:3]:
            if not isinstance(item, dict):
                continue
            name = item.get("provider")
            if not isinstance(name, str) or name not in {"DuckDuckGo", "Bing", "Google"}:
                continue
            attempts = item.get("attempts", [])
            safe_attempts = []
            if isinstance(attempts, list):
                for attempt in attempts[:6]:
                    if not isinstance(attempt, dict):
                        continue
                    variant = attempt.get("variant")
                    endpoint = attempt.get("endpoint_variant")
                    error = attempt.get("error")
                    safe_attempts.append({
                        "variant": variant if isinstance(variant, str) and variant in {"primary", "alternate_1", "alternate_2"} else "other",
                        "endpoint_variant": endpoint if isinstance(endpoint, str) and endpoint in {"html", "lite", "standard", "basic_html"} else "other",
                        "error": error if isinstance(error, str) and error in {
                            "challenge_or_consent_page", "challenge_page",
                            "no_parseable_results", "SourceFetchError",
                        } else ("other" if error else None),
                    })
            def count(field: str) -> int:
                raw = item.get(field, 0)
                return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0
            providers.append({
                "provider": name,
                "attempt_count": len(safe_attempts),
                "parsed_result_count": count("parsed_result_count"),
                "accepted_result_count": count("accepted_result_count"),
                "unique_result_count": count("unique_result_count"),
                "duplicate_result_count": count("duplicate_result_count"),
                "attempts": safe_attempts,
            })
    fetch = value.get("fetch", {})
    if not isinstance(fetch, dict):
        fetch = {}
    def fetch_count(field: str) -> int:
        raw = fetch.get(field, 0)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0
    raw_failures = fetch.get("failure_counts", {})
    failures = {}
    if isinstance(raw_failures, dict):
        for key, raw in list(raw_failures.items())[:5]:
            if key in {"SourceFetchError", "TimeoutError", "URLError", "OSError"}:
                if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                    failures[key] = raw
    return {
        "providers": providers,
        "fetch": {
            "candidate_count": fetch_count("candidate_count"),
            "attempted_count": fetch_count("attempted_count"),
            "fetched_count": fetch_count("fetched_count"),
            "failure_counts": failures,
        },
    }


def _review_summary(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(review, dict):
        return None
    claims = review.get("claims", [])
    claims = claims if isinstance(claims, list) else []
    verdict_counts: dict[str, int] = {}
    for claim in claims[:30]:
        if isinstance(claim, dict):
            verdict = claim.get("verdict")
            if verdict in {"supported", "contradicted", "insufficient_evidence"}:
                verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    conflicts = review.get("source_conflicts", [])
    conflicts = conflicts if isinstance(conflicts, list) else []
    safe_conflicts = []
    for conflict in conflicts[:10]:
        if not isinstance(conflict, dict):
            continue
        source_a = conflict.get("source_a_url")
        source_b = conflict.get("source_b_url")
        if (
            isinstance(source_a, str) and source_a.startswith("https://") and len(source_a) <= 1000
            and isinstance(source_b, str) and source_b.startswith("https://") and len(source_b) <= 1000
            and source_a != source_b
        ):
            safe_conflicts.append({"source_a_url": source_a, "source_b_url": source_b})
    error = review.get("error")
    return {
        "passed": review.get("passed") is True,
        "overall": str(review.get("overall", "insufficient_evidence"))[:50],
        "claim_count": len(claims),
        "verdict_counts": verdict_counts,
        "source_conflict_count": len(safe_conflicts),
        "source_conflict_pairs": safe_conflicts,
        "error": str(error)[:120] if error else None,
        "reviewer": str(review.get("reviewer", "local_ollama_model_assisted_not_independent"))[:100],
        "source_discovery": _source_discovery_summary(review.get("source_discovery")),
    }


def _memory_references(memory: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(memory, dict):
        return []
    refs: list[dict[str, Any]] = []
    for kind, status in (("verified_experience_guidance", "verified"), ("failure_warnings", "warning")):
        items = memory.get(kind, [])
        if not isinstance(items, list):
            continue
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            version = item.get("version", 1)
            if isinstance(item_id, int) and not isinstance(item_id, bool) and item_id > 0:
                refs.append({
                    "id": item_id,
                    "version": int(version) if isinstance(version, int) and version > 0 else 1,
                    "status": status,
                    "kind": kind,
                })
    return refs[:6]


def record_research_run(
    *,
    question: str,
    answer: str,
    sources: list[dict[str, Any]],
    review_status: str,
    review: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    duration_ms: int = 0,
) -> int:
    """Persist a local audit row without storing raw query, answer, or source excerpt text."""
    if review_status not in _ALLOWED_REVIEW_STATUSES:
        raise ValueError("Unknown research review status.")
    if not isinstance(question, str) or not isinstance(answer, str):
        raise TypeError("Question and answer must be strings.")
    duration = max(0, min(86_400_000, int(duration_ms)))
    source_rows = _source_metadata(sources if isinstance(sources, list) else [])
    review_row = _review_summary(review)
    memory_rows = _memory_references(memory)
    with learning_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO chat_research_runs(
                   query_sha256, answer_sha256, route, source_count, sources_json,
                   review_status, review_summary_json, memory_refs_json, duration_ms
               ) VALUES (?, ?, 'technical_research', ?, ?, ?, ?, ?, ?)""",
            (
                _sha256(question[:12000]),
                _sha256(answer[:12000]),
                len(source_rows),
                json.dumps(source_rows, ensure_ascii=False, separators=(",", ":")),
                review_status,
                json.dumps(review_row, ensure_ascii=False, separators=(",", ":")) if review_row else None,
                json.dumps(memory_rows, ensure_ascii=False, separators=(",", ":")),
                duration,
            ),
        )
        return int(cursor.lastrowid)
