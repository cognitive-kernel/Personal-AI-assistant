"""Deterministic release gate for evidence-backed learning content.

This gate validates provenance and review contracts. It deliberately does not claim
that an LLM-assisted review is independent proof of scientific truth.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

GATE_SCHEMA_VERSION = 1
_MODEL_REVIEWER = "local_ollama_model_assisted_not_independent"


def evaluate_scientific_gate(
    *,
    source_kind: str,
    sources: list[dict[str, Any]],
    claim_review: dict[str, Any] | None,
    lesson: str,
) -> dict[str, Any]:
    """Return a fail-closed evidence-gate result without making a truth claim."""
    reasons: list[str] = []
    normalized_sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    hosts: set[str] = set()

    for source in sources[:8]:
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        title = source.get("title")
        excerpt = source.get("content")
        if not isinstance(url, str) or not isinstance(title, str) or not isinstance(excerpt, str):
            continue
        try:
            parsed = urlsplit(url)
            valid_url = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.port != 0
            )
        except ValueError:
            valid_url = False
            parsed = None
        if not valid_url or parsed is None or len(excerpt.strip()) < 40:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        hosts.add(parsed.hostname.lower().rstrip("."))
        normalized_sources.append({
            "url": url,
            "title": title[:250],
            "content": excerpt.strip()[:2500],
        })

    if source_kind != "web_retrieved":
        reasons.append("external_retrieved_sources_required")
    if len(normalized_sources) < 2:
        reasons.append("at_least_two_usable_sources_required")
    if len(hosts) < 2:
        reasons.append("sources_must_come_from_two_distinct_hosts")
    if not isinstance(lesson, str) or not lesson.strip():
        reasons.append("lesson_missing")

    if not isinstance(claim_review, dict):
        reasons.append("claim_review_missing")
    else:
        if claim_review.get("passed") is not True:
            reasons.append("claim_review_did_not_pass")
        if claim_review.get("reviewer") != _MODEL_REVIEWER:
            reasons.append("reviewer_identity_missing_or_unrecognized")
        claims = claim_review.get("claims")
        if not isinstance(claims, list) or not claims:
            reasons.append("claim_review_has_no_claims")
        elif any(
            not isinstance(claim, dict)
            or claim.get("verdict") != "supported"
            or not isinstance(claim.get("evidence_urls"), list)
            or not claim.get("evidence_urls")
            or any(url not in seen_urls for url in claim.get("evidence_urls", []))
            for claim in claims
        ):
            reasons.append("claim_evidence_contract_failed")
        conflicts = claim_review.get("source_conflicts")
        if not isinstance(conflicts, list):
            reasons.append("source_conflict_check_missing")
        elif conflicts:
            reasons.append("unresolved_source_conflicts")

    # The gate also requires exact source URLs to appear in the lesson text.
    cited_urls = [url for url in seen_urls if url in lesson] if isinstance(lesson, str) else []
    if not cited_urls:
        reasons.append("no_exact_source_url_citation_in_lesson")

    passed = not reasons
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "status": "machine_reviewed_not_independently_verified" if passed else "blocked",
        "passed": passed,
        "independently_verified": False,
        "source_count": len(normalized_sources),
        "distinct_source_host_count": len(hosts),
        "exact_cited_source_count": len(cited_urls),
        "reviewer": _MODEL_REVIEWER if isinstance(claim_review, dict) else None,
        "reasons": reasons,
        "note": (
            "Automated evidence and citation checks passed; this is not independent proof of scientific truth."
            if passed else
            "Scientific release is blocked because one or more deterministic evidence requirements are unmet."
        ),
    }
