"""Autonomous discovery of public cloud-compute options.

Discovery records evidence only. Public pages are untrusted input; no account
creation, credential submission, quota bypass, code execution, or provisioning
is performed by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from .config import defaults
from .cloud_provider_adapters import extract_resource_claims
from .cloud_resource_policy import assess_candidate
from .schema_registry import CLOUD_RESOURCE_MIGRATIONS, initialize_database_file
from . import task_manager, web_sources

CLOUD_DISCOVERY_TASK_TYPE = "cloud.discover_resources"
MAX_RESULTS_PER_QUERY = 5
MAX_CANDIDATES_PER_RUN = 25
MAX_EVIDENCE_EXCERPT = 600

DISCOVERY_QUERIES = (
    "free cloud hosting compute resources free plan quotas official",
    "official free cloud GPU compute notebook free tier terms",
    "official free cloud CPU virtual machine compute credits free tier",
    "official always free cloud virtual machine monthly compute quota",
    "official free developer sandbox container compute limits",
)

# Used only when general search returns no relevant compute-resource evidence.
# These are public provider/product documentation pages, not proof of account
# eligibility, live capacity, or permission to execute remote work.
OFFICIAL_REFERENCE_SOURCES = (
    (
        "https://research.google.com/colaboratory/faq.html",
        "Google Colaboratory FAQ",
    ),
    (
        "https://www.oracle.com/cloud/free/faq/",
        "Oracle Cloud Free Tier FAQ",
    ),
    (
        "https://docs.github.com/en/billing/concepts/product-billing/github-codespaces",
        "GitHub Codespaces billing and included usage",
    ),
)


class CloudResourceError(RuntimeError):
    """Raised when cloud-resource discovery storage or input is invalid."""


class CloudResourceNotFoundError(CloudResourceError):
    """Raised when a review targets a missing candidate."""


class CloudResourceConflictError(CloudResourceError):
    """Raised when evidence changed or is too stale to review safely."""


def _sqlite_candidate_relevant(title: Any, excerpt: Any) -> int:
    """Reapply the source-evidence relevance predicate inside SQLite."""
    signals = _resource_signals(str(title or ""), str(excerpt or ""))
    return int(_has_cloud_compute_relevance(signals))


def _sqlite_candidate_rank(source_url: Any, title: Any) -> int:
    return _candidate_source_rank({
        "source_url": str(source_url or ""),
        "title": str(title or ""),
    })


def _sqlite_source_identity(source_url: Any) -> str:
    url = str(source_url or "")
    try:
        return web_sources.result_url_identity(url)
    except (ValueError, TypeError):
        return "invalid-source:" + url


def _connect() -> sqlite3.Connection:
    target = Path(defaults.cloud_resources_db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.create_function("cloud_candidate_relevant", 2, _sqlite_candidate_relevant, deterministic=True)
    connection.create_function("cloud_candidate_rank", 2, _sqlite_candidate_rank, deterministic=True)
    connection.create_function("cloud_source_identity", 1, _sqlite_source_identity, deterministic=True)
    return connection


def initialize_store() -> int:
    """Initialize the dedicated cloud-resource database using versioned migrations."""
    return initialize_database_file(
        defaults.cloud_resources_db_path,
        CLOUD_RESOURCE_MIGRATIONS,
    )


KAGGLE_PROVIDER_ID = "kaggle"
KAGGLE_QUOTA_CONSENT_VERSION = "kaggle-quota-readonly-v1"


def get_provider_consent(provider_id: str = KAGGLE_PROVIDER_ID) -> dict[str, Any]:
    if provider_id != KAGGLE_PROVIDER_ID:
        raise CloudResourceError("Unknown provider consent scope.")
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT provider_id, enabled, user_consent, executable_path, consent_version, "
            "consented_at, revoked_at, updated_at FROM provider_access_consents WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
    if row is None:
        return {
            "provider_id": provider_id,
            "enabled": False,
            "user_consent": False,
            "executable_path": "",
            "consent_version": KAGGLE_QUOTA_CONSENT_VERSION,
            "consented_at": None,
            "revoked_at": None,
            "updated_at": None,
        }
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["user_consent"] = bool(result["user_consent"])
    return result


def record_provider_consent(
    *,
    enabled: bool,
    user_consent: bool,
    executable_path: str = "",
    provider_id: str = KAGGLE_PROVIDER_ID,
) -> dict[str, Any]:
    """Record an explicit local opt-in/revocation without storing credentials."""
    if provider_id != KAGGLE_PROVIDER_ID:
        raise CloudResourceError("Unknown provider consent scope.")
    if type(enabled) is not bool or type(user_consent) is not bool:
        raise CloudResourceError("Consent and enablement must be explicit booleans.")
    if enabled and not user_consent:
        raise CloudResourceError("Provider access cannot be enabled without explicit consent.")
    if enabled:
        if not isinstance(executable_path, str) or len(executable_path) > 2048:
            raise CloudResourceError("A valid absolute Kaggle CLI executable path is required.")
        executable = Path(executable_path).expanduser()
        if not executable.is_absolute() or not executable.is_file():
            raise CloudResourceError("A valid absolute Kaggle CLI executable path is required.")
        normalized_path = str(executable)
    else:
        if user_consent:
            raise CloudResourceError("Revocation must disable access and clear consent.")
        normalized_path = ""

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT enabled, executable_path FROM provider_access_consents WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
        if enabled:
            changed = existing is None or not bool(existing["enabled"]) or existing["executable_path"] != normalized_path
            if changed:
                connection.execute(
                    "INSERT INTO provider_access_consent_events(provider_id, event_type, consent_version) "
                    "VALUES (?, 'consent_granted', ?)",
                    (provider_id, KAGGLE_QUOTA_CONSENT_VERSION),
                )
            connection.execute(
                """INSERT INTO provider_access_consents(
                       provider_id, enabled, user_consent, executable_path, consent_version,
                       consented_at, revoked_at, updated_at
                   ) VALUES (?, 1, 1, ?, ?, ?, NULL, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET
                       enabled=1, user_consent=1, executable_path=excluded.executable_path,
                       consent_version=excluded.consent_version,
                       consented_at=CASE WHEN provider_access_consents.enabled=1
                                             AND provider_access_consents.executable_path=excluded.executable_path
                                         THEN provider_access_consents.consented_at
                                         ELSE excluded.consented_at END,
                       revoked_at=NULL, updated_at=excluded.updated_at""",
                (provider_id, normalized_path, KAGGLE_QUOTA_CONSENT_VERSION, now, now),
            )
        elif existing is not None and bool(existing["enabled"]):
            connection.execute(
                "INSERT INTO provider_access_consent_events(provider_id, event_type, consent_version) "
                "VALUES (?, 'consent_revoked', ?)",
                (provider_id, KAGGLE_QUOTA_CONSENT_VERSION),
            )
            connection.execute(
                """UPDATE provider_access_consents
                   SET enabled=0, user_consent=0, executable_path='',
                       revoked_at=?, updated_at=? WHERE provider_id=?""",
                (now, now, provider_id),
            )
            # Account-specific quota values are removed on revocation; the consent audit remains.
            connection.execute(
                "DELETE FROM provider_quota_snapshots WHERE provider_id=?",
                (provider_id,),
            )
        connection.commit()
    return get_provider_consent(provider_id)


def list_provider_consent_events(
    provider_id: str = KAGGLE_PROVIDER_ID,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if provider_id != KAGGLE_PROVIDER_ID:
        raise CloudResourceError("Unknown provider consent scope.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CloudResourceError("Consent event limit must be between 1 and 100.")
    with closing(_connect()) as connection:
        rows = connection.execute(
            """SELECT id, provider_id, event_type, consent_version, created_at
               FROM provider_access_consent_events WHERE provider_id=?
               ORDER BY id DESC LIMIT ?""",
            (provider_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def save_provider_quota_snapshot(snapshot: Any, provider_id: str = KAGGLE_PROVIDER_ID) -> list[dict[str, Any]]:
    """Persist only parsed quota numbers, never raw CLI output or credentials."""
    if provider_id != KAGGLE_PROVIDER_ID:
        raise CloudResourceError("Unknown provider quota scope.")
    if not hasattr(snapshot, "observed_at") or not hasattr(snapshot, "refresh_at"):
        raise CloudResourceError("Quota snapshot has an invalid shape.")
    consent = get_provider_consent(provider_id)
    if not consent["enabled"] or not consent["user_consent"]:
        raise CloudResourceError("Quota cannot be stored without current provider consent.")
    rows: list[tuple[str, float, float, float]] = []
    for resource_name in ("GPU", "TPU"):
        quota = getattr(snapshot, resource_name.lower(), None)
        if quota is None:
            continue
        values = (quota.used_hours, quota.remaining_hours, quota.total_hours)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise CloudResourceError("Quota snapshot contains invalid numeric values.")
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise CloudResourceError("Quota snapshot contains invalid numeric values.")
        rows.append((resource_name, float(values[0]), float(values[1]), float(values[2])))
    if not rows:
        raise CloudResourceError("Quota snapshot contains no known GPU or TPU quota.")
    observed_at = snapshot.observed_at.astimezone(timezone.utc).isoformat()
    refresh_at = snapshot.refresh_at.astimezone(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT enabled, user_consent FROM provider_access_consents WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
        if current is None or not current["enabled"] or not current["user_consent"]:
            connection.rollback()
            raise CloudResourceError("Provider consent was revoked before quota persistence.")
        connection.execute("DELETE FROM provider_quota_snapshots WHERE provider_id=?", (provider_id,))
        for resource_name, used, remaining, total in rows:
            connection.execute(
                """INSERT INTO provider_quota_snapshots(
                       provider_id, resource, used_hours, remaining_hours, total_hours,
                       observed_at, refresh_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (provider_id, resource_name, used, remaining, total, observed_at, refresh_at),
            )
        connection.commit()
    return list_provider_quota_snapshots(provider_id)


def list_provider_quota_snapshots(
    provider_id: str = KAGGLE_PROVIDER_ID,
) -> list[dict[str, Any]]:
    if provider_id != KAGGLE_PROVIDER_ID:
        raise CloudResourceError("Unknown provider quota scope.")
    consent = get_provider_consent(provider_id)
    if not consent["enabled"] or not consent["user_consent"]:
        return []
    with closing(_connect()) as connection:
        rows = connection.execute(
            """SELECT resource, used_hours, remaining_hours, total_hours, observed_at, refresh_at
               FROM provider_quota_snapshots WHERE provider_id=?
               ORDER BY resource""",
            (provider_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _normalize_source_url(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or len(url) > 2048:
        raise CloudResourceError("Discovered source URL is invalid.")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise CloudResourceError("Cloud resource sources must use public HTTPS URLs.")
    hostname = parsed.hostname.rstrip(".").lower()
    normalized = urlunparse(parsed._replace(fragment=""))
    return normalized, hostname


_SPECIFIC_COMPUTE_SERVICE_PATTERNS = (
    # Require product/infra language, not an incidental mention of hardware.
    r"\b(?:cloud|hosted)\s+(?:jupyter\s+)?notebooks?\b",
    r"\bcloud\s+hosting\b",
    r"\bcloud\s+servers?\b",
    r"\b(?:cloud\s+vps|virtual private servers?)\b",
    r"\b(?:compute|cloud)\s+(?:virtual )?machines?\b",
    r"\b(?:virtual machines?|vm instances?|compute instances?|codespaces?|container runtimes?|developer sandboxes?)\b",
    r"\b(?:gpu|tpu|cpu)\s+(?:compute|resources?|instances?|hours?|credits?|quotas?|runtime|access)\b",
    # "compute services" alone is a broad catalog phrase, not a concrete offer.
    r"\bcompute\s+(?:resources?|instances?|hours?|credits?|quotas?|runtime)\b",
    r"\b(?:ocpu|vcpu)\b",
)
_SPECIFIC_COMPUTE_OFFER_PATTERNS = (
    r"\balways\s+free\b",
    r"\bfree\s+tier\b",
    r"\bfree\s+of\s+charge\b",
    r"\bfree\s+(?:compute|gpu|tpu|cpu|credits?|resources?|runtime|notebooks?)\b",
    r"\bfree\s+(?:cloud\s+)?hosting\b",
    r"\bfree\s+(?:plan|account|instance|vm|server|container)\b",
    r"\bmonthly\s+(?:usage\s+)?quotas?\b",
    r"\busage\s+quotas?\s+included\b",
    r"\b(?:runtime|compute)\s+limits?\b",
    r"\b(?:compute|gpu|tpu)\s+hours?\s+per\s+month\b",
    r"\bcredits?\s+per\s+month\b",
    r"\btrial\s+credits?\b",
)


def _segment_matches_specific_compute_offer(segment: str) -> bool:
    """Check that one title/sentence/block mentions both a compute service and its offer."""
    return bool(
        any(re.search(pattern, segment, flags=re.IGNORECASE) for pattern in _SPECIFIC_COMPUTE_SERVICE_PATTERNS)
        and any(re.search(pattern, segment, flags=re.IGNORECASE) for pattern in _SPECIFIC_COMPUTE_OFFER_PATTERNS)
    )


def _specific_compute_offer_segments(text: str) -> list[str]:
    """Return only page blocks/sentences that independently support compute-offer relevance."""
    segments = re.split(r"[\r\n]+|(?<=[.!?;])\s+", text)
    return [
        " ".join(segment.split())
        for segment in segments
        if segment.strip() and _segment_matches_specific_compute_offer(segment)
    ]


def _has_specific_compute_offer_evidence(title: str, text: str) -> bool:
    """Require compute and offer wording in one specific title, sentence, or page block.

    Broad catalog phrases such as "Free Compute Services" are insufficient by
    themselves. A page must identify a more concrete compute service in its
    title or have a matching sentence/block in its body.
    """
    title_segment = " ".join(title.split())
    return (
        _segment_matches_specific_compute_offer(title_segment)
        or bool(_specific_compute_offer_segments(text))
    )


def _resource_signals(title: str, text: str) -> list[str]:
    searchable = f"{title} {text}".casefold()
    rules = {
        "gpu_or_accelerator_mentioned": ("gpu", "cuda", "tpu", "accelerator"),
        "cpu_compute_mentioned": ("cpu", "vcpu", "compute"),
        "free_tier_or_credits_mentioned": ("free tier", "free", "credits", "trial"),
        "quota_or_limit_mentioned": ("quota", "limit", "usage cap", "rate limit"),
        "terms_or_policy_mentioned": ("terms", "acceptable use", "policy", "conditions"),
        "notebook_environment_mentioned": ("notebook", "jupyter", "colab"),
        "vm_or_container_mentioned": ("virtual machine", " vm ", "container", "sandbox"),
        "cloud_hosting_mentioned": ("cloud hosting", "cloud server", "cloud vps", "virtual private server", "container hosting"),
        "storage_mentioned": ("storage", "disk", "persistent volume"),
    }
    padded = f" {searchable} "
    signals = {
        signal for signal, phrases in rules.items()
        if any(phrase in padded for phrase in phrases)
    }
    if _has_specific_compute_offer_evidence(title, text):
        signals.add("specific_compute_offer_pattern_matched")
    return sorted(signals)


_COMPUTE_EVIDENCE_SIGNALS = frozenset({
    "gpu_or_accelerator_mentioned",
    "cpu_compute_mentioned",
    "notebook_environment_mentioned",
    "vm_or_container_mentioned",
    "cloud_hosting_mentioned",
})
_OFFER_EVIDENCE_SIGNALS = frozenset({
    "free_tier_or_credits_mentioned",
    "quota_or_limit_mentioned",
    "terms_or_policy_mentioned",
})


def _has_cloud_compute_relevance(signals: Any) -> bool:
    """Require technical compute and offer signals plus specific service/offer wording.

    The specific-pattern marker is deliberately persisted with the candidate so
    listing also hides broad-signal false positives saved by older versions.
    """
    if not isinstance(signals, (list, tuple, set)):
        return False
    observed = set(signals)
    return bool(
        "specific_compute_offer_pattern_matched" in observed
        and observed.intersection(_COMPUTE_EVIDENCE_SIGNALS)
        and observed.intersection(_OFFER_EVIDENCE_SIGNALS)
    )


def _candidate_record(document: web_sources.SourceDocument, query: str) -> dict[str, Any] | None:
    try:
        url, domain = _normalize_source_url(document.url)
    except (CloudResourceError, ValueError):
        return None
    title = " ".join(str(document.title).split())[:300]
    raw_text = str(document.text)[:12000]
    text = " ".join(raw_text.split())
    if not title or len(text) < 80:
        return None
    # Evaluate on unflattened text so unrelated HTML blocks do not combine.
    signals = _resource_signals(title, raw_text)
    if not _has_cloud_compute_relevance(signals):
        return None
    matching_evidence = _specific_compute_offer_segments(raw_text)
    if matching_evidence:
        excerpt = "\n".join(matching_evidence)[:MAX_EVIDENCE_EXCERPT]
    elif _segment_matches_specific_compute_offer(title):
        # Make title-only matching explicit instead of implying the body confirmed it.
        title_note = f"Title-only relevance match: {title}\nPage text follows:\n"
        excerpt = (title_note + text)[:MAX_EVIDENCE_EXCERPT]
    else:
        excerpt = text[:MAX_EVIDENCE_EXCERPT]
    return {
        "source_url": url,
        "provider_domain": domain,
        "title": title,
        "content_sha256": hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest(),
        "resource_signals_json": json.dumps(
            signals, ensure_ascii=False, separators=(",", ":")
        ),
        "evidence_excerpt": excerpt,
        "normalized_text": text,
        "discovery_query": query[:300],
    }


def _update_run(
    run_id: int,
    *,
    status: str,
    queries_attempted: int,
    sources_seen: int,
    candidates_upserted: int,
    query_results: list[dict[str, Any]],
    error_summary: str | None,
) -> None:
    with closing(_connect()) as connection:
        connection.execute(
            """UPDATE cloud_resource_discovery_runs
               SET finished_at=CURRENT_TIMESTAMP, status=?, queries_attempted=?,
                   sources_seen=?, candidates_upserted=?, query_results_json=?,
                   error_summary=?
               WHERE id=?""",
            (
                status,
                queries_attempted,
                sources_seen,
                candidates_upserted,
                json.dumps(query_results, ensure_ascii=False, separators=(",", ":")),
                error_summary[:1000] if error_summary else None,
                run_id,
            ),
        )
        connection.commit()


def discover_resources(
    *,
    discover_fn: Callable[..., list[web_sources.SourceDocument]] | None = None,
    checkpoint: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Search a bounded set of topics and persist source-backed candidates as unreviewed."""
    initialize_store()
    search = discover_fn or web_sources.discover_sources
    with closing(_connect()) as connection:
        cursor = connection.execute(
            "INSERT INTO cloud_resource_discovery_runs(status) VALUES ('running')"
        )
        run_id = int(cursor.lastrowid)
        connection.commit()

    query_results: list[dict[str, Any]] = []
    unique: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    queries_attempted = 0
    sources_seen = 0

    try:
        for index, query in enumerate(DISCOVERY_QUERIES):
            if checkpoint is not None:
                checkpoint({"phase": "cloud_discovery_query", "query_index": index})
            queries_attempted += 1
            try:
                documents = search(query, limit=MAX_RESULTS_PER_QUERY)
                if not isinstance(documents, list):
                    raise CloudResourceError("Source discovery returned an invalid result list.")
            except Exception as exc:
                reason = " ".join(str(exc).split())[:180]
                errors.append(f"query_{index + 1}:{type(exc).__name__}:{reason}")
                failed_query_result: dict[str, Any] = {
                    "query": query,
                    "result_count": 0,
                    "error": f"{type(exc).__name__}: {reason}",
                }
                provider_diagnostics = getattr(exc, "provider_diagnostics", None)
                if isinstance(provider_diagnostics, list):
                    failed_query_result["provider_diagnostics"] = provider_diagnostics
                query_results.append(failed_query_result)
                continue

            query_result: dict[str, Any] = {
                "query": query,
                "result_count": len(documents),
                "error": None,
            }
            provider_diagnostics = getattr(documents, "search_provider_diagnostics", None)
            if isinstance(provider_diagnostics, list):
                query_result["provider_diagnostics"] = provider_diagnostics
            sources_seen += len(documents)
            checked_documents = 0
            relevant_documents = 0
            irrelevant_documents = 0
            duplicate_candidates = 0
            skipped_due_to_candidate_cap = 0
            for document in documents:
                if not isinstance(document, web_sources.SourceDocument):
                    continue
                if len(unique) >= MAX_CANDIDATES_PER_RUN:
                    skipped_due_to_candidate_cap += 1
                    continue
                checked_documents += 1
                candidate = _candidate_record(document, query)
                if candidate is None:
                    irrelevant_documents += 1
                    continue
                relevant_documents += 1
                identity = web_sources.result_url_identity(candidate["source_url"])
                if identity in unique:
                    duplicate_candidates += 1
                    continue
                unique[identity] = candidate
            query_result["candidate_filter"] = {
                "documents_checked": checked_documents,
                "relevant_documents": relevant_documents,
                "rejected_as_irrelevant": irrelevant_documents,
                "duplicate_candidates_suppressed": duplicate_candidates,
                "not_checked_due_to_candidate_cap": skipped_due_to_candidate_cap,
            }
            query_results.append(query_result)

        # A public search engine can return technically valid but irrelevant
        # pages (for example lyrics pages for a cloud-compute query). Do not
        # persist those results. If none qualify, try a tiny set of official
        # references through the same URL/redirect/content safety checks.
        if not unique:
            if sources_seen:
                errors.append("search_results_filtered_as_irrelevant")
            fallback_query = "official_reference_fallback"
            queries_attempted += 1
            fallback_documents = 0
            fallback_relevant = 0
            fallback_irrelevant = 0
            fallback_duplicates = 0
            fallback_errors: list[str] = []
            for source_url, title_hint in OFFICIAL_REFERENCE_SOURCES:
                try:
                    document = web_sources.fetch_public_document(source_url, title_hint)
                except Exception as exc:
                    reason = " ".join(str(exc).split())[:140]
                    host = urlparse(source_url).hostname or "unknown"
                    fallback_errors.append(f"{host}:{type(exc).__name__}:{reason}")
                    continue
                if not isinstance(document, web_sources.SourceDocument):
                    fallback_errors.append("invalid_source_document")
                    continue
                fallback_documents += 1
                sources_seen += 1
                candidate = _candidate_record(document, fallback_query)
                if candidate is None:
                    fallback_irrelevant += 1
                    continue
                fallback_relevant += 1
                identity = web_sources.result_url_identity(candidate["source_url"])
                if identity in unique:
                    fallback_duplicates += 1
                    continue
                unique[identity] = candidate
            query_results.append({
                "query": fallback_query,
                "result_count": fallback_documents,
                "error": "; ".join(fallback_errors)[:500] if fallback_errors else None,
                "candidate_filter": {
                    "documents_checked": fallback_documents,
                    "relevant_documents": fallback_relevant,
                    "rejected_as_irrelevant": fallback_irrelevant,
                    "duplicate_candidates_suppressed": fallback_duplicates,
                },
            })
            if fallback_errors:
                errors.append(
                    "official_reference_fallback:" + "; ".join(fallback_errors)
                )

        with closing(_connect()) as connection:
            for candidate in unique.values():
                previous = connection.execute(
                    "SELECT id, content_sha256 FROM cloud_resource_candidates WHERE source_url=?",
                    (candidate["source_url"],),
                ).fetchone()
                if previous is not None and previous["content_sha256"] != candidate["content_sha256"]:
                    connection.execute(
                        "UPDATE cloud_resource_claims SET is_current=0 WHERE candidate_id=?",
                        (previous["id"],),
                    )
                connection.execute(
                    """INSERT INTO cloud_resource_candidates(
                           source_url, provider_domain, title, content_sha256,
                           resource_signals_json, evidence_excerpt, discovery_query
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_url) DO UPDATE SET
                           provider_domain=excluded.provider_domain,
                           title=excluded.title,
                           content_sha256=excluded.content_sha256,
                           resource_signals_json=excluded.resource_signals_json,
                           evidence_excerpt=excluded.evidence_excerpt,
                           discovery_query=excluded.discovery_query,
                           review_status=CASE
                               WHEN cloud_resource_candidates.content_sha256=excluded.content_sha256
                                AND cloud_resource_candidates.reviewed_content_sha256=cloud_resource_candidates.content_sha256
                               THEN cloud_resource_candidates.review_status ELSE 'unreviewed' END,
                           availability_status=CASE
                               WHEN cloud_resource_candidates.content_sha256=excluded.content_sha256
                                AND cloud_resource_candidates.reviewed_content_sha256=cloud_resource_candidates.content_sha256
                                AND cloud_resource_candidates.freshness_status!='stale'
                               THEN cloud_resource_candidates.availability_status ELSE 'unknown' END,
                           quota_json=CASE
                               WHEN cloud_resource_candidates.content_sha256=excluded.content_sha256
                                AND cloud_resource_candidates.reviewed_content_sha256=cloud_resource_candidates.content_sha256
                               THEN cloud_resource_candidates.quota_json ELSE '{}' END,
                           terms_url=CASE
                               WHEN cloud_resource_candidates.content_sha256=excluded.content_sha256
                                AND cloud_resource_candidates.reviewed_content_sha256=cloud_resource_candidates.content_sha256
                               THEN cloud_resource_candidates.terms_url ELSE NULL END,
                           reviewed_at=CASE
                               WHEN cloud_resource_candidates.content_sha256=excluded.content_sha256
                                AND cloud_resource_candidates.reviewed_content_sha256=cloud_resource_candidates.content_sha256
                               THEN cloud_resource_candidates.reviewed_at ELSE NULL END,
                           reviewed_content_sha256=CASE
                               WHEN cloud_resource_candidates.content_sha256=excluded.content_sha256
                                AND cloud_resource_candidates.reviewed_content_sha256=cloud_resource_candidates.content_sha256
                               THEN cloud_resource_candidates.reviewed_content_sha256 ELSE NULL END,
                           freshness_status='fresh',
                           last_seen_at=CURRENT_TIMESTAMP,
                           updated_at=CURRENT_TIMESTAMP""",
                    (
                        candidate["source_url"],
                        candidate["provider_domain"],
                        candidate["title"],
                        candidate["content_sha256"],
                        candidate["resource_signals_json"],
                        candidate["evidence_excerpt"],
                        candidate["discovery_query"],
                    ),
                )
                candidate_row = connection.execute(
                    "SELECT id FROM cloud_resource_candidates WHERE source_url=?",
                    (candidate["source_url"],),
                ).fetchone()
                candidate_id = int(candidate_row["id"])
                claims = extract_resource_claims(
                    candidate["provider_domain"],
                    candidate["title"],
                    candidate["normalized_text"],
                    source_url=candidate["source_url"],
                )
                for claim in claims:
                    value_json = json.dumps(
                        claim.value,
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """INSERT INTO cloud_resource_claims(
                               candidate_id, source_content_sha256, claim_type, value_json,
                               unit, evidence_quote, adapter_id, is_current
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                           ON CONFLICT(candidate_id, source_content_sha256, claim_type,
                                       value_json, evidence_quote)
                           DO UPDATE SET is_current=1""",
                        (
                            candidate_id,
                            candidate["content_sha256"],
                            claim.claim_type,
                            value_json,
                            claim.unit,
                            claim.evidence_quote,
                            claim.adapter_id,
                        ),
                    )
            _mark_stale_candidates(connection, max_age_days=30)
            connection.commit()

        if errors and unique:
            status = "partial"
        elif unique:
            status = "completed"
        else:
            status = "failed"
            errors.append(
                "No relevant valid public HTTPS cloud-compute source documents were returned."
            )
    except Exception as exc:
        errors.append(f"discovery_aborted:{type(exc).__name__}")
        _update_run(
            run_id,
            status="partial" if unique else "failed",
            queries_attempted=queries_attempted,
            sources_seen=sources_seen,
            candidates_upserted=0,
            query_results=query_results,
            error_summary="; ".join(errors),
        )
        raise

    _update_run(
        run_id,
        status=status,
        queries_attempted=queries_attempted,
        sources_seen=sources_seen,
        candidates_upserted=len(unique),
        query_results=query_results,
        error_summary="; ".join(errors) if errors else None,
    )
    return {
        "run_id": run_id,
        "status": status,
        "queries_attempted": queries_attempted,
        "sources_seen": sources_seen,
        "candidates_upserted": len(unique),
        "error_summary": "; ".join(errors) if errors else None,
    }


def _mark_stale_candidates(connection: sqlite3.Connection, *, max_age_days: int) -> int:
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or not 1 <= max_age_days <= 3650:
        raise ValueError("max_age_days must be between 1 and 3650.")
    cursor = connection.execute(
        """UPDATE cloud_resource_candidates
           SET freshness_status='stale', availability_status='unknown',
               updated_at=CURRENT_TIMESTAMP
           WHERE last_seen_at < datetime('now', ?)
             AND freshness_status != 'stale'""",
        (f"-{max_age_days} days",),
    )
    return max(0, int(cursor.rowcount))


def mark_stale_candidates(*, max_age_days: int = 30) -> int:
    """Mark old source observations stale and clear availability claims."""
    initialize_store()
    with closing(_connect()) as connection:
        changed = _mark_stale_candidates(connection, max_age_days=max_age_days)
        connection.commit()
    return changed


# Domains are intentionally specific. Hosting platforms such as github.io are
# excluded so a user-published article is not mistaken for provider documentation.
_RECOGNIZED_PROVIDER_DOMAIN_ROOTS = (
    "aws.amazon.com",
    "azure.microsoft.com",
    "learn.microsoft.com",
    "cloud.google.com",
    "research.google.com",
    "oracle.com",
    "docs.oracle.com",
    "cloud.oracle.com",
    "docs.github.com",
    "developer.nvidia.com",
    "docs.nvidia.com",
    "modal.com",
    "runpod.io",
    "nebius.com",
    "paperspace.com",
    "lambda.ai",
    "digitalocean.com",
    "developers.cloudflare.com",
    "cloudflare.com",
    "cloud.ibm.com",
    "docs.vultr.com",
    "vultr.com",
    "linode.com",
    "docs.hetzner.com",
    "hetzner.com",
    "ovhcloud.com",
    "alibabacloud.com",
)
_OFFICIAL_REFERENCE_PATH_MARKERS = (
    "/docs/",
    "/documentation",
    "/pricing",
    "/billing",
    "/terms",
    "/policy",
    "/policies",
    "/faq",
    "/free/",
    "/free-",
    "/free_",
    "freetier",
    "free-tier",
    "/quota",
    "/usage",
)


def _is_recognized_provider_domain(domain: Any) -> bool:
    """Return whether a host matches a known provider-owned documentation/product domain.

    This is a display and ordering heuristic only; it does not verify page contents,
    account eligibility, current capacity, or whether any usage is free.
    """
    if not isinstance(domain, str) or not domain.strip():
        return False
    host = domain.rstrip(".").lower()
    return any(
        host == root or host.endswith("." + root)
        for root in _RECOGNIZED_PROVIDER_DOMAIN_ROOTS
    )


# Heuristics for separating likely editorial/roundup pages from provider-owned pages.
# These labels are navigation hints only; they do not prove page ownership.
_INTERMEDIARY_PATH_MARKERS = (
    "/blog", "/article", "/tutorial", "/guide", "/comparison",
    "/compare", "/buyingguides", "/posts", "/news",
)
_INTERMEDIARY_TITLE_PATTERNS = (
    r"\bhow\s+to\b",
    r"\bguide\b",
    r"\btutorial\b",
    r"\bcompar(?:e|ison|ing)\b",
    r"\bvs\.?\b",
    r"\bbest\s+\d+\b",
    r"\btop\s+\d+\b",
    r"\bplatforms?\s+compared\b",
    r"\breview\b",
)


def _candidate_source_classification(item: dict[str, Any]) -> str:
    """Classify source shape conservatively for display and ordering only."""
    source_url = str(item.get("source_url") or "")
    parsed = urlparse(source_url)
    if _is_recognized_provider_domain(parsed.hostname):
        return "recognized_provider_domain"

    path = parsed.path.casefold()
    title = str(item.get("title") or "").casefold()
    if (
        any(marker in path for marker in _INTERMEDIARY_PATH_MARKERS)
        or any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in _INTERMEDIARY_TITLE_PATTERNS)
    ):
        return "likely_intermediary_article"
    return "unclassified_unverified_domain"


def _candidate_source_rank(item: dict[str, Any]) -> int:
    """Prefer official references, other recognized domains, unknown pages, then likely articles."""
    source_url = str(item.get("source_url") or "")
    parsed = urlparse(source_url)
    if _is_recognized_provider_domain(parsed.hostname):
        path = parsed.path.casefold()
        if any(marker in path for marker in _OFFICIAL_REFERENCE_PATH_MARKERS):
            return 0
        return 1
    if _candidate_source_classification(item) == "likely_intermediary_article":
        return 3
    return 2


def _serialize_candidate(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["resource_signals"] = json.loads(item.pop("resource_signals_json"))
    item["quota"] = json.loads(item.pop("quota_json"))
    return item


def list_candidates(*, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Apply relevance, deduplication, ranking, and pagination in SQLite."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500.")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 50_000:
        raise ValueError("offset must be between 0 and 50000.")
    initialize_store()
    with closing(_connect()) as connection:
        _mark_stale_candidates(connection, max_age_days=30)
        official_domain_conditions = []
        official_domain_params: list[str] = []
        for root in _RECOGNIZED_PROVIDER_DOMAIN_ROOTS:
            official_domain_conditions.append(
                "(lower(c.provider_domain)=? OR lower(c.provider_domain) LIKE ?)"
            )
            official_domain_params.extend((root, f"%.{root}"))
        official_domain_clause = " OR ".join(official_domain_conditions)
        reference_path_clause = " OR ".join(
            "lower(c.source_url) LIKE ?" for _ in _OFFICIAL_REFERENCE_PATH_MARKERS
        )
        reference_path_params = [
            f"%{marker.casefold()}%" for marker in _OFFICIAL_REFERENCE_PATH_MARKERS
        ]
        discovery_rank = (
            f"CASE WHEN ({official_domain_clause}) THEN "
            f"CASE WHEN ({reference_path_clause}) THEN 0 ELSE 1 END ELSE 2 END"
        )
        rows = connection.execute(
            f"""WITH marked AS (
                    SELECT c.*,
                           cloud_source_identity(c.source_url) AS _source_identity,
                           cloud_candidate_rank(c.source_url, c.title) AS _source_rank,
                           {discovery_rank} AS _discovery_rank,
                           ROW_NUMBER() OVER (
                               PARTITION BY cloud_source_identity(c.source_url)
                               ORDER BY {discovery_rank} ASC, c.last_seen_at DESC, c.id DESC
                           ) AS _identity_rank
                    FROM cloud_resource_candidates AS c
                    WHERE cloud_candidate_relevant(c.title, c.evidence_excerpt)=1
                ),
                deduped AS (
                    SELECT * FROM marked WHERE _identity_rank=1
                )
                SELECT * FROM deduped
                ORDER BY _source_rank ASC, last_seen_at DESC, id DESC
                LIMIT ? OFFSET ?""",
            (
                *official_domain_params, *reference_path_params,
                *official_domain_params, *reference_path_params,
                limit, offset,
            ),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            raw.pop("_source_identity", None)
            raw.pop("_source_rank", None)
            raw.pop("_discovery_rank", None)
            raw.pop("_identity_rank", None)
            item = _serialize_candidate(raw)
            # Preserve the prior contract: reflect signals recalculated from the
            # stored excerpt, rather than potentially stale JSON from an old run.
            item["resource_signals"] = _resource_signals(
                str(item.get("title") or ""),
                str(item.get("evidence_excerpt") or ""),
            )
            parsed_host = urlparse(str(item.get("source_url") or "")).hostname
            item["source_authority"] = (
                "recognized_official_domain"
                if _is_recognized_provider_domain(parsed_host)
                else "unverified_domain"
            )
            item["source_classification"] = _candidate_source_classification(item)
            claim_rows = connection.execute(
                """SELECT id, source_content_sha256, claim_type, value_json, unit,
                          evidence_quote, adapter_id, review_status, is_current, extracted_at,
                          reviewed_at
                   FROM cloud_resource_claims
                   WHERE candidate_id=? AND is_current=1
                   ORDER BY claim_type, id""",
                (item["id"],),
            ).fetchall()
            item["observed_claims"] = []
            for claim in claim_rows:
                claim_item = dict(claim)
                claim_item["value"] = json.loads(claim_item.pop("value_json"))
                item["observed_claims"].append(claim_item)
            item["eligibility_assessment"] = assess_candidate(item, item["observed_claims"])
            results.append(item)
        connection.commit()
    return results

def save_resource_selection_plan(plan: Any) -> dict[str, Any]:
    """Persist a minimal local snapshot of a dry-run plan and per-candidate decisions."""
    initialize_store()
    if not isinstance(plan, dict):
        raise CloudResourceError("Resource selection plan must be an object.")
    plan_version = plan.get("plan_version")
    status = plan.get("status")
    requirements = plan.get("requirements")
    limitations = plan.get("limitations", [])
    decisions = plan.get("candidates")
    selected_id = plan.get("selected_candidate_id")
    if plan_version != "provider-neutral-selection-v1":
        raise CloudResourceError("Resource selection plan version is unsupported.")
    if status not in {"candidate_found", "no_eligible_candidate"}:
        raise CloudResourceError("Resource selection plan status is invalid.")
    if (
        not isinstance(requirements, dict)
        or not isinstance(limitations, list)
        or any(not isinstance(item, str) for item in limitations)
        or not isinstance(decisions, list)
    ):
        raise CloudResourceError("Resource selection plan requirements, limitations, or decisions are invalid.")
    if plan.get("execution_authorized") is not False or plan.get("remote_execution_enabled") is not False:
        raise CloudResourceError("A dry-run plan cannot authorize remote execution.")
    if selected_id is not None and (
        isinstance(selected_id, bool) or not isinstance(selected_id, int) or selected_id < 1
    ):
        raise CloudResourceError("Selected candidate ID is invalid.")

    try:
        requirements_json = json.dumps(
            requirements, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        limitations_json = json.dumps(
            limitations, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise CloudResourceError("Plan requirements or limitations are not safe JSON data.") from exc

    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """INSERT INTO cloud_resource_selection_plans(
                   plan_version, requirements_json, limitations_json, status, selected_candidate_id,
                   execution_authorized, remote_execution_enabled
               ) VALUES (?, ?, ?, ?, ?, 0, 0)""",
            (plan_version, requirements_json, limitations_json, status, selected_id),
        )
        plan_id = int(cursor.lastrowid)
        for decision in decisions:
            if not isinstance(decision, dict):
                raise CloudResourceError("Plan contains an invalid candidate decision.")
            candidate_id = decision.get("candidate_id")
            if candidate_id is not None and (
                isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1
            ):
                raise CloudResourceError("Plan candidate ID is invalid.")
            domain = decision.get("provider_domain")
            content_hash = decision.get("content_sha256")
            if domain is not None and (not isinstance(domain, str) or len(domain) > 255):
                raise CloudResourceError("Plan provider domain is invalid.")
            if content_hash is not None and (
                not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", content_hash)
            ):
                raise CloudResourceError("Plan candidate content hash is invalid.")
            eligible = decision.get("eligible_for_adapter_review")
            if type(eligible) is not bool:
                raise CloudResourceError("Plan eligibility decision must be a boolean.")
            preflight = decision.get("preflight_status")
            if preflight not in {"blocked", "needs_review", "ready_for_provider_adapter"}:
                raise CloudResourceError("Plan preflight status is invalid.")
            json_fields = {}
            for field in (
                "capabilities", "reasons", "policy_blockers", "policy_review_needed"
            ):
                value = decision.get(field)
                expected_type = dict if field == "capabilities" else list
                if not isinstance(value, expected_type):
                    raise CloudResourceError(f"Plan field {field} has an invalid shape.")
                try:
                    json_fields[field] = json.dumps(
                        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
                    )
                except (TypeError, ValueError) as exc:
                    raise CloudResourceError(f"Plan field {field} is not safe JSON data.") from exc
            connection.execute(
                """INSERT INTO cloud_resource_selection_decisions(
                       plan_id, candidate_id, provider_domain, content_sha256,
                       eligible_for_adapter_review, preflight_status, capabilities_json,
                       reasons_json, policy_blockers_json, policy_review_needed_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id, candidate_id, domain, content_hash, int(eligible), preflight,
                    json_fields["capabilities"], json_fields["reasons"],
                    json_fields["policy_blockers"], json_fields["policy_review_needed"],
                ),
            )
        connection.commit()
    return get_resource_selection_plan(plan_id)


def get_resource_selection_plan(plan_id: int) -> dict[str, Any]:
    initialize_store()
    if isinstance(plan_id, bool) or not isinstance(plan_id, int) or plan_id < 1:
        raise ValueError("plan_id must be a positive integer.")
    with closing(_connect()) as connection:
        row = connection.execute(
            """SELECT id, plan_version, requirements_json, limitations_json, status, selected_candidate_id,
                      execution_authorized, remote_execution_enabled, created_at
               FROM cloud_resource_selection_plans WHERE id=?""",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise CloudResourceNotFoundError("Resource selection plan was not found.")
        decision_rows = connection.execute(
            """SELECT candidate_id, provider_domain, content_sha256, eligible_for_adapter_review,
                      preflight_status, capabilities_json, reasons_json, policy_blockers_json,
                      policy_review_needed_json, evaluated_at
               FROM cloud_resource_selection_decisions WHERE plan_id=? ORDER BY id""",
            (plan_id,),
        ).fetchall()
    return {
        "plan_id": int(row["id"]),
        "plan_version": row["plan_version"],
        "requirements": json.loads(row["requirements_json"]),
        "limitations": json.loads(row["limitations_json"]),
        "status": row["status"],
        "selected_candidate_id": row["selected_candidate_id"],
        "execution_authorized": False,
        "remote_execution_enabled": False,
        "created_at": row["created_at"],
        "candidates": [
            {
                "candidate_id": item["candidate_id"],
                "provider_domain": item["provider_domain"],
                "content_sha256": item["content_sha256"],
                "eligible_for_adapter_review": bool(item["eligible_for_adapter_review"]),
                "preflight_status": item["preflight_status"],
                "capabilities": json.loads(item["capabilities_json"]),
                "reasons": json.loads(item["reasons_json"]),
                "policy_blockers": json.loads(item["policy_blockers_json"]),
                "policy_review_needed": json.loads(item["policy_review_needed_json"]),
                "evaluated_at": item["evaluated_at"],
                "execution_authorized": False,
            }
            for item in decision_rows
        ],
    }


def list_resource_selection_plans(*, limit: int = 20) -> list[dict[str, Any]]:
    initialize_store()
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT id FROM cloud_resource_selection_plans ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [get_resource_selection_plan(int(row["id"])) for row in rows]


def review_candidate(
    candidate_id: int,
    *,
    expected_content_sha256: str,
    review_status: str,
    availability_status: str = "unknown",
    quota: dict[str, Any] | None = None,
    terms_url: str | None = None,
) -> dict[str, Any]:
    """Review current evidence using an optimistic hash check; stale content cannot be approved."""
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer.")
    if not isinstance(expected_content_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_content_sha256
    ):
        raise ValueError("expected_content_sha256 must be a lowercase SHA-256 digest.")
    if review_status not in {"approved", "rejected"}:
        raise ValueError("review_status must be approved or rejected.")
    if availability_status not in {"unknown", "available", "unavailable", "requires_auth"}:
        raise ValueError("availability_status is invalid.")
    if quota is None:
        quota = {}
    if not isinstance(quota, dict):
        raise ValueError("quota must be a JSON object.")
    try:
        quota_json = json.dumps(quota, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("quota must contain only finite JSON values.") from exc
    normalized_terms_url = None
    if terms_url is not None:
        normalized_terms_url, _ = _normalize_source_url(terms_url)
    if review_status == "approved" and not normalized_terms_url:
        raise ValueError("An HTTPS terms_url is required to approve a provider candidate.")
    if review_status == "rejected" and availability_status == "available":
        raise ValueError("A rejected provider candidate cannot be marked available.")

    initialize_store()
    with closing(_connect()) as connection:
        _mark_stale_candidates(connection, max_age_days=30)
        row = connection.execute(
            "SELECT * FROM cloud_resource_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise CloudResourceNotFoundError(f"Cloud resource candidate {candidate_id} does not exist.")
        if row["content_sha256"] != expected_content_sha256:
            raise CloudResourceConflictError("Source content changed; refresh the candidate before reviewing.")
        if row["freshness_status"] != "fresh":
            raise CloudResourceConflictError("Candidate evidence is stale; run discovery again before reviewing.")
        connection.execute(
            """UPDATE cloud_resource_candidates
               SET review_status=?, availability_status=?, quota_json=?, terms_url=?,
                   reviewed_at=CURRENT_TIMESTAMP, reviewed_content_sha256=content_sha256,
                   freshness_status='fresh', updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                review_status,
                availability_status,
                quota_json,
                normalized_terms_url,
                candidate_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM cloud_resource_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
        connection.commit()
    return _serialize_candidate(updated)

def review_claim(
    candidate_id: int,
    claim_id: int,
    *,
    expected_source_content_sha256: str,
    review_status: str,
) -> dict[str, Any]:
    """Accept/reject one extracted claim only for the current reviewed source version."""
    for name, value in (("candidate_id", candidate_id), ("claim_id", claim_id)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer.")
    if not isinstance(expected_source_content_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_source_content_sha256
    ):
        raise ValueError("expected_source_content_sha256 must be a lowercase SHA-256 digest.")
    if review_status not in {"accepted", "rejected"}:
        raise ValueError("review_status must be accepted or rejected.")

    initialize_store()
    with closing(_connect()) as connection:
        _mark_stale_candidates(connection, max_age_days=30)
        row = connection.execute(
            """SELECT claim.id, claim.source_content_sha256, claim.claim_type,
                      claim.value_json, claim.unit, claim.evidence_quote, claim.adapter_id,
                      claim.review_status, claim.is_current, claim.extracted_at, claim.reviewed_at,
                      candidate.content_sha256 AS candidate_content_sha256,
                      candidate.review_status AS candidate_review_status,
                      candidate.freshness_status AS candidate_freshness_status
               FROM cloud_resource_claims AS claim
               JOIN cloud_resource_candidates AS candidate
                 ON candidate.id=claim.candidate_id
               WHERE claim.id=? AND claim.candidate_id=?""",
            (claim_id, candidate_id),
        ).fetchone()
        if row is None:
            raise CloudResourceNotFoundError(
                f"Claim {claim_id} for candidate {candidate_id} does not exist."
            )
        if (
            row["is_current"] != 1
            or row["source_content_sha256"] != expected_source_content_sha256
            or row["candidate_content_sha256"] != expected_source_content_sha256
        ):
            raise CloudResourceConflictError(
                "Claim evidence changed; refresh the candidate before reviewing."
            )
        if row["candidate_freshness_status"] != "fresh":
            raise CloudResourceConflictError(
                "Candidate evidence is stale; run discovery again before reviewing claims."
            )
        if review_status == "accepted" and row["candidate_review_status"] != "approved":
            raise CloudResourceConflictError(
                "Review and approve the candidate source before accepting an extracted claim."
            )
        cursor = connection.execute(
            """UPDATE cloud_resource_claims
               SET review_status=?, reviewed_at=CURRENT_TIMESTAMP
               WHERE id=? AND candidate_id=? AND is_current=1
                 AND source_content_sha256=?""",
            (
                review_status,
                claim_id,
                candidate_id,
                expected_source_content_sha256,
            ),
        )
        if cursor.rowcount != 1:
            raise CloudResourceConflictError("Claim changed during review; reload and try again.")
        updated = connection.execute(
            """SELECT id, source_content_sha256, claim_type, value_json, unit,
                      evidence_quote, adapter_id, review_status, is_current, extracted_at,
                      reviewed_at
               FROM cloud_resource_claims WHERE id=?""",
            (claim_id,),
        ).fetchone()
        connection.commit()
    item = dict(updated)
    item["value"] = json.loads(item.pop("value_json"))
    return item



def latest_discovery_run() -> dict[str, Any] | None:
    initialize_store()
    with closing(_connect()) as connection:
        row = connection.execute(
            """SELECT * FROM cloud_resource_discovery_runs
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["query_results"] = json.loads(item.pop("query_results_json"))
    return item


def _enqueue_with_key(
    key: str,
    *,
    trigger: str,
    scheduled_at: datetime,
) -> dict[str, Any]:
    return task_manager.create_task(
        CLOUD_DISCOVERY_TASK_TYPE,
        {"trigger": trigger},
        priority=-10,
        max_attempts=1,
        scheduled_at=scheduled_at,
        dedupe_key=key,
    )


def enqueue_discovery_task(*, manual: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if manual:
        key = "cloud-discovery-manual:" + now.strftime("%Y%m%dT%H%M%S%fZ")
        return _enqueue_with_key(key, trigger="manual", scheduled_at=now)
    key = f"cloud-discovery:{now.date().isoformat()}"
    return _enqueue_with_key(key, trigger="scheduled", scheduled_at=now)


def ensure_daily_discovery_scheduled() -> dict[str, Any]:
    """Idempotently ensure one discovery task exists for the current UTC day."""
    initialize_store()
    now = datetime.now(timezone.utc)
    day_key = f"cloud-discovery:{now.date().isoformat()}"
    existing = task_manager.get_task_by_dedupe_key(day_key)
    if existing is not None and existing["status"] not in {"failed", "cancelled"}:
        return existing
    if existing is None:
        return _enqueue_with_key(day_key, trigger="scheduled", scheduled_at=now)

    # If a queued run was explicitly cancelled before it could chain tomorrow's
    # task, allow one recovery run after the next application startup.
    recovery_key = f"{day_key}:recovery"
    recovery = task_manager.get_task_by_dedupe_key(recovery_key)
    if recovery is not None:
        return recovery
    return _enqueue_with_key(recovery_key, trigger="scheduled", scheduled_at=now)


def _schedule_next_daily_run() -> tuple[dict[str, Any], datetime]:
    next_run = datetime.now(timezone.utc) + timedelta(days=1)
    next_key = f"cloud-discovery:{next_run.date().isoformat()}"
    next_task = task_manager.create_task(
        CLOUD_DISCOVERY_TASK_TYPE,
        {"trigger": "scheduled"},
        priority=-10,
        max_attempts=1,
        scheduled_at=next_run,
        dedupe_key=next_key,
    )
    return next_task, next_run


def handle_discovery_task(
    payload: dict[str, Any],
    checkpoint_data: dict[str, Any] | None,
    context: Any,
) -> dict[str, Any]:
    """Run discovery under durable task controls and schedule the next daily pass."""
    try:
        report = discover_resources(checkpoint=context.checkpoint)
    except Exception:
        # Keep the daily chain alive even when a task is paused/cancelled or discovery fails.
        _schedule_next_daily_run()
        raise
    next_task, next_run = _schedule_next_daily_run()
    return {
        **report,
        "next_scheduled_task_id": next_task["id"],
        "next_scheduled_at": next_run.isoformat(),
    }



OCI_PROVIDER_ID = "oracle_cloud"
OCI_LIMIT_CONSENT_VERSION = "oci-readonly-limits-v1"


def get_oci_limit_consent() -> dict[str, Any]:
    with closing(_connect()) as connection:
        row = connection.execute(
            """SELECT provider_id, enabled, user_consent, executable_path, config_file,
                      profile, region, compartment_id, consent_version, consented_at,
                      revoked_at, updated_at
               FROM oci_limit_access_consents WHERE provider_id=?""",
            (OCI_PROVIDER_ID,),
        ).fetchone()
    if row is None:
        return {
            "provider_id": OCI_PROVIDER_ID,
            "enabled": False,
            "user_consent": False,
            "executable_path": "",
            "config_file": "",
            "profile": "",
            "region": "",
            "compartment_id": "",
            "consent_version": OCI_LIMIT_CONSENT_VERSION,
            "consented_at": None,
            "revoked_at": None,
            "updated_at": None,
        }
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["user_consent"] = bool(result["user_consent"])
    return result


def record_oci_limit_consent(
    *,
    enabled: bool,
    user_consent: bool,
    executable_path: str = "",
    config_file: str = "",
    profile: str = "",
    region: str = "",
    compartment_id: str = "",
) -> dict[str, Any]:
    """Record explicit consent for read-only OCI service-limit checks, never credentials."""
    if type(enabled) is not bool or type(user_consent) is not bool:
        raise CloudResourceError("Consent and enablement must be explicit booleans.")
    if enabled and not user_consent:
        raise CloudResourceError("OCI access cannot be enabled without explicit user consent.")
    if enabled:
        if not isinstance(executable_path, str) or len(executable_path) > 2048:
            raise CloudResourceError("A valid absolute OCI CLI executable path is required.")
        if not isinstance(config_file, str) or len(config_file) > 2048:
            raise CloudResourceError("A valid absolute OCI config file path is required.")
        executable = Path(executable_path).expanduser()
        config = Path(config_file).expanduser()
        if not executable.is_absolute() or not executable.is_file():
            raise CloudResourceError("A valid absolute OCI CLI executable path is required.")
        if not config.is_absolute() or not config.is_file():
            raise CloudResourceError("A valid absolute OCI config file path is required.")
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
            raise CloudResourceError("OCI profile contains unsupported characters.")
        if not isinstance(region, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}[a-z0-9]", region):
            raise CloudResourceError("OCI region contains unsupported characters.")
        if not isinstance(compartment_id, str) or not re.fullmatch(
            r"ocid1\.(?:tenancy|compartment)\.[A-Za-z0-9-]+\.[A-Za-z0-9._-]+",
            compartment_id,
        ):
            raise CloudResourceError("OCI compartment ID must be a valid tenancy/compartment OCID.")
        values = (
            str(executable), str(config), profile, region, compartment_id
        )
    else:
        if user_consent:
            raise CloudResourceError("Revocation must disable access and clear consent.")
        values = ("", "", "", "", "")

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """SELECT enabled, executable_path, config_file, profile, region, compartment_id
               FROM oci_limit_access_consents WHERE provider_id=?""",
            (OCI_PROVIDER_ID,),
        ).fetchone()
        if enabled:
            changed = existing is None or not bool(existing["enabled"]) or tuple(
                existing[key] for key in (
                    "executable_path", "config_file", "profile", "region", "compartment_id"
                )
            ) != values
            if changed:
                connection.execute(
                    """INSERT INTO oci_limit_consent_events(provider_id, event_type, consent_version)
                       VALUES (?, 'consent_granted', ?)""",
                    (OCI_PROVIDER_ID, OCI_LIMIT_CONSENT_VERSION),
                )
            connection.execute(
                """INSERT INTO oci_limit_access_consents(
                       provider_id, enabled, user_consent, executable_path, config_file,
                       profile, region, compartment_id, consent_version, consented_at,
                       revoked_at, updated_at
                   ) VALUES (?, 1, 1, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                   ON CONFLICT(provider_id) DO UPDATE SET
                       enabled=1, user_consent=1, executable_path=excluded.executable_path,
                       config_file=excluded.config_file, profile=excluded.profile,
                       region=excluded.region, compartment_id=excluded.compartment_id,
                       consent_version=excluded.consent_version,
                       consented_at=CASE
                           WHEN oci_limit_access_consents.enabled=1
                            AND oci_limit_access_consents.executable_path=excluded.executable_path
                            AND oci_limit_access_consents.config_file=excluded.config_file
                            AND oci_limit_access_consents.profile=excluded.profile
                            AND oci_limit_access_consents.region=excluded.region
                            AND oci_limit_access_consents.compartment_id=excluded.compartment_id
                           THEN oci_limit_access_consents.consented_at
                           ELSE excluded.consented_at END,
                       revoked_at=NULL, updated_at=excluded.updated_at""",
                (
                    OCI_PROVIDER_ID, *values, OCI_LIMIT_CONSENT_VERSION, now, now
                ),
            )
        elif existing is not None and bool(existing["enabled"]):
            connection.execute(
                """INSERT INTO oci_limit_consent_events(provider_id, event_type, consent_version)
                   VALUES (?, 'consent_revoked', ?)""",
                (OCI_PROVIDER_ID, OCI_LIMIT_CONSENT_VERSION),
            )
            connection.execute(
                """UPDATE oci_limit_access_consents
                   SET enabled=0, user_consent=0, executable_path='', config_file='',
                       profile='', region='', compartment_id='', revoked_at=?, updated_at=?
                   WHERE provider_id=?""",
                (now, now, OCI_PROVIDER_ID),
            )
            connection.execute(
                "DELETE FROM oci_limit_snapshots WHERE provider_id=?",
                (OCI_PROVIDER_ID,),
            )
        connection.commit()
    return get_oci_limit_consent()


def list_oci_limit_consent_events(*, limit: int = 20) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CloudResourceError("OCI consent event limit must be between 1 and 100.")
    with closing(_connect()) as connection:
        rows = connection.execute(
            """SELECT id, provider_id, event_type, consent_version, created_at
               FROM oci_limit_consent_events WHERE provider_id=?
               ORDER BY id DESC LIMIT ?""",
            (OCI_PROVIDER_ID, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def save_oci_limit_snapshot(snapshot: Any) -> list[dict[str, Any]]:
    """Persist only parsed OCI limit numbers and scopes, never raw CLI output."""
    if not hasattr(snapshot, "observed_at") or not hasattr(snapshot, "region") or not hasattr(snapshot, "limits"):
        raise CloudResourceError("OCI limit snapshot has an invalid shape.")
    if not isinstance(snapshot.observed_at, datetime) or snapshot.observed_at.tzinfo is None:
        raise CloudResourceError("OCI limit snapshot requires a timezone-aware observation time.")
    consent = get_oci_limit_consent()
    if not consent["enabled"] or not consent["user_consent"]:
        raise CloudResourceError("OCI limit snapshot cannot be stored without current consent.")
    if snapshot.region != consent["region"]:
        raise CloudResourceError("OCI snapshot region does not match the consented region.")
    if not isinstance(snapshot.limits, (list, tuple)) or not snapshot.limits:
        raise CloudResourceError("OCI limit snapshot contains no limits.")
    rows: list[tuple[str, float, float | None, float | None, str, str]] = []
    for item in snapshot.limits:
        name = getattr(item, "name", None)
        value = getattr(item, "value", None)
        used = getattr(item, "used", None)
        available = getattr(item, "available", None)
        scope_type = getattr(item, "scope_type", None) or ""
        scope_value = getattr(item, "scope_value", None) or ""
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise CloudResourceError("OCI limit name is invalid.")
        for field, number in (("value", value), ("used", used), ("available", available)):
            if number is None and field != "value":
                continue
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise CloudResourceError(f"OCI limit {field} must be numeric.")
            try:
                finite = math.isfinite(float(number))
            except (OverflowError, TypeError, ValueError):
                finite = False
            if not finite or number < 0:
                raise CloudResourceError(f"OCI limit {field} must be finite and non-negative.")
        if not isinstance(scope_type, str) or not isinstance(scope_value, str):
            raise CloudResourceError("OCI limit scope fields must be strings.")
        rows.append((
            name.strip(), float(value), None if used is None else float(used),
            None if available is None else float(available),
            scope_type[:80], scope_value[:200],
        ))
    observed_at = snapshot.observed_at.astimezone(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            """SELECT enabled, user_consent, region FROM oci_limit_access_consents
               WHERE provider_id=?""",
            (OCI_PROVIDER_ID,),
        ).fetchone()
        if (
            current is None or not current["enabled"] or not current["user_consent"]
            or current["region"] != snapshot.region
        ):
            connection.rollback()
            raise CloudResourceError("OCI consent changed before limit snapshot persistence.")
        connection.execute("DELETE FROM oci_limit_snapshots WHERE provider_id=?", (OCI_PROVIDER_ID,))
        for name, value, used, available, scope_type, scope_value in rows:
            connection.execute(
                """INSERT INTO oci_limit_snapshots(
                       provider_id, resource_name, limit_value, used_value, available_value,
                       scope_type, scope_value, region, observed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (OCI_PROVIDER_ID, name, value, used, available, scope_type, scope_value,
                 snapshot.region, observed_at),
            )
        connection.commit()
    return list_oci_limit_snapshots()


def list_oci_limit_snapshots() -> list[dict[str, Any]]:
    consent = get_oci_limit_consent()
    if not consent["enabled"] or not consent["user_consent"]:
        return []
    with closing(_connect()) as connection:
        rows = connection.execute(
            """SELECT resource_name, limit_value, used_value, available_value,
                      scope_type, scope_value, region, observed_at
               FROM oci_limit_snapshots WHERE provider_id=?
               ORDER BY resource_name, scope_type, scope_value""",
            (OCI_PROVIDER_ID,),
        ).fetchall()
    return [dict(row) for row in rows]
