"""Fail-closed, auditable evidence gate for generated lessons.

A passing result means the lesson met the configured evidence/provenance rules.
It is not a proof of scientific truth: source authority and claim entailment cannot
be established by URL shape or a second call to the same model alone.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

GATE_VERSION = "lesson-evidence-v1"


def _https_source(source: Any) -> tuple[str, str] | None:
    if not isinstance(source, dict):
        return None
    url = source.get("url")
    excerpt = source.get("content")
    if not isinstance(url, str) or not isinstance(excerpt, str) or len(excerpt.strip()) < 40:
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    return url, parsed.hostname.casefold().rstrip(".")


def evaluate_lesson_evidence(
    lesson: str,
    sources: list[dict[str, Any]],
    claim_review: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a deterministic acceptance decision and per-gate reasons.

    This intentionally does not use a numeric quality score. Any missing or
    malformed evidence fails closed; model-assisted review is treated only as
    one necessary signal, never as independent scientific verification.
    """
    usable: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for source in sources if isinstance(sources, list) else []:
        parsed = _https_source(source)
        if parsed and parsed[0] not in seen_urls:
            usable.append(parsed)
            seen_urls.add(parsed[0])

    cited = {
        url for url, _host in usable
        if isinstance(lesson, str) and url in lesson
    }
    hosts = {host for url, host in usable if url in cited}
    review = claim_review if isinstance(claim_review, dict) else {}
    claims = review.get("claims")
    conflicts = review.get("source_conflicts", [])
    all_claims_supported = (
        isinstance(claims, list)
        and len(claims) > 0
        and all(
            isinstance(claim, dict)
            and claim.get("verdict") == "supported"
            and isinstance(claim.get("evidence_urls"), list)
            and len(claim["evidence_urls"]) > 0
            and all(url in seen_urls for url in claim["evidence_urls"])
            for claim in claims
        )
    )
    gates = {
        "at_least_two_distinct_https_sources": len(cited) >= 2 and len(hosts) >= 2,
        "every_used_source_cited_exactly": len(cited) >= 2,
        "claim_review_passed": review.get("passed") is True and review.get("overall") == "supported",
        "all_claims_supported_by_known_sources": all_claims_supported,
        "no_reported_source_conflicts": isinstance(conflicts, list) and len(conflicts) == 0,
    }
    passed = all(gates.values())
    return {
        "version": GATE_VERSION,
        "status": "evidence_gate_passed_not_proof_of_truth" if passed else "not_accepted",
        "accepted": passed,
        "independent_scientific_verification": False,
        "source_count": len(usable),
        "cited_source_count": len(cited),
        "distinct_cited_host_count": len(hosts),
        "gates": gates,
        "failed_gates": [name for name, value in gates.items() if not value],
        "limitations": [
            "HTTPS and domain diversity do not establish source authority.",
            "Claim review is model-assisted and is not an independent proof of truth.",
            "Passing this gate is not a substitute for reproducible domain-specific tests.",
        ],
    }
