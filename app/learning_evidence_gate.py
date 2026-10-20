"""Fail-closed, auditable evidence gate for generated lessons.

A passing result means the lesson met the configured evidence/provenance rules.
It is not a proof of scientific truth: source authority and claim entailment cannot
be established by URL shape or a second call to the same model alone.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

GATE_VERSION = "lesson-evidence-v2"
# Curated primary/official domains only. This is deliberately conservative and
# must be extended through reviewed code changes, not model-generated trust.
_TRUSTED_DOMAIN_ROOTS = {
    "python.org", "microsoft.com", "mozilla.org", "ietf.org", "rfc-editor.org",
    "nist.gov", "cisa.gov", "owasp.org", "w3.org", "iso.org", "ncbi.nlm.nih.gov",
    "who.int", "cdc.gov", "nature.com", "science.org", "acm.org", "ieee.org",
    "arxiv.org", "gov.uk", "europa.eu", "sec.gov", "finra.org",
    "federalreserve.gov", "cftc.gov", "esma.europa.eu", "mikrotik.com",
    "vmware.com", "cisco.com", "ollama.com", "postgresql.org", "sqlite.org",
    "kernel.org", "debian.org", "ubuntu.com", "docs.docker.com",
}


def is_allowlisted_primary_source(url: str) -> bool:
    """Return whether an HTTPS URL belongs to a curated primary-source domain."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.casefold().rstrip(".")
    except ValueError:
        return False
    return any(host == root or host.endswith("." + root) for root in _TRUSTED_DOMAIN_ROOTS)


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


def _exact_cited_urls(lesson: str) -> set[str]:
    """Extract complete URL citation tokens, ignoring only trailing citation punctuation."""
    if not isinstance(lesson, str):
        return set()
    found: set[str] = set()
    for match in re.finditer(r"https?://\S+", lesson):
        token = match.group(0).rstrip(".,;:!?)]}>" + chr(34) + chr(39))
        if token:
            found.add(token)
    return found


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

    cited_tokens = _exact_cited_urls(lesson)
    cited = {url for url, _host in usable if url in cited_tokens}
    hosts = {host for url, host in usable if url in cited}
    trusted_cited_hosts = {
        host for url, host in usable
        if url in cited and is_allowlisted_primary_source(url)
    }
    review = claim_review if isinstance(claim_review, dict) else {}
    claims = review.get("claims")
    conflicts = review.get("source_conflicts")
    all_claims_supported = (
        isinstance(claims, list)
        and len(claims) > 0
        and all(
            isinstance(claim, dict)
            and claim.get("verdict") == "supported"
            and isinstance(claim.get("evidence_urls"), list)
            and len(claim["evidence_urls"]) > 0
            and all(isinstance(url, str) and url in cited for url in claim["evidence_urls"])
            for claim in claims
        )
    )
    gates = {
        "at_least_two_distinct_https_sources": len(cited) >= 2 and len(hosts) >= 2,
        "at_least_two_source_urls_cited_exactly": len(cited) >= 2,
        "at_least_one_allowlisted_primary_source": len(trusted_cited_hosts) >= 1,
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
        "trusted_cited_host_count": len(trusted_cited_hosts),
        "gates": gates,
        "failed_gates": [name for name, value in gates.items() if not value],
        "limitations": [
            "Only domains on a conservative, code-reviewed allowlist count as primary-source candidates; allowlisting does not prove relevance or truth.",
            "Claim review is model-assisted and is not an independent proof of truth.",
            "Passing this gate is not a substitute for reproducible domain-specific tests.",
        ],
    }
