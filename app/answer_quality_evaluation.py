"""Conservative surface-level checks for bilingual answer-quality runs.

These checks are diagnostics only. They are not semantic adjudication or a model-quality score.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .learning_evidence_gate import is_allowlisted_primary_source


def normalize_text(value: str) -> str:
    """Normalize common Persian/Arabic letter variants for transparent substring checks."""
    text = value.casefold().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def evaluate_answer_surface(case: dict[str, Any], answer: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Return auditable lexical indicators; do not imply semantic correctness."""
    if not isinstance(answer, str):
        raise ValueError("answer must be a string")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    normalized_answer = normalize_text(answer)
    concepts = case["expected_concepts"]
    prohibited = case["must_not_claim"]
    matched = [concept for concept in concepts if normalize_text(concept) in normalized_answer]
    missing = [concept for concept in concepts if normalize_text(concept) not in normalized_answer]
    prohibited_hits = [claim for claim in prohibited if normalize_text(claim) in normalized_answer]
    has_persian = bool(re.search(r"[\u0600-\u06ff]", answer))
    has_latin = bool(re.search(r"[A-Za-z]", answer))
    expected_language_marker = has_persian if case["language"] == "fa" else has_latin
    source_urls = []
    for source in sources:
        if isinstance(source, dict):
            url = source.get("url")
            if isinstance(url, str) and url.startswith("https://") and url not in source_urls:
                source_urls.append(url)
    # Model-written links are not retrieval evidence unless they match the structured
    # source metadata exactly. This is a triage signal, not proof of fabrication.
    answer_urls = []
    for match in re.findall(r"https://\S+", answer):
        url = match.rstrip(".,;:!?)]}")
        if url and url not in answer_urls:
            answer_urls.append(url)
    unattributed_answer_urls = [url for url in answer_urls if url not in source_urls]
    return {
        "case_id": case["id"],
        "language": case["language"],
        "answer_chars": len(answer),
        "expected_concepts_count": len(concepts),
        "surface_matched_concepts": matched,
        "surface_missing_concepts": missing,
        "surface_concept_coverage": round(len(matched) / len(concepts), 4) if concepts else 0.0,
        "prohibited_phrase_hits": prohibited_hits,
        "expected_language_script_present": expected_language_marker,
        "source_urls": source_urls,
        "source_count": len(source_urls),
        "trusted_source_count": sum(1 for url in source_urls if is_allowlisted_primary_source(url)),
        "answer_urls": answer_urls,
        "unattributed_answer_urls": unattributed_answer_urls,
        "unattributed_answer_url_count": len(unattributed_answer_urls),
        "interpretation": (
            "Lexical indicators only; paraphrases may be missed and phrase presence does not prove correctness."
        ),
    }


def summarize_surface_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize diagnostics by language while exposing failed cases and denominators."""
    if not results:
        raise ValueError("results must not be empty")
    summary: dict[str, Any] = {"case_count": len(results), "languages": {}, "interpretation": (
        "Not a model-quality score. Automated lexical/provenance indicators only; a pinned live-model run and all acceptance gates are required. Human scoring is not used, and passing is not proof of scientific truth."
    )}
    for language in sorted({str(item["language"]) for item in results}):
        items = [item for item in results if item["language"] == language]
        concept_total = sum(int(item["expected_concepts_count"]) for item in items)
        concept_matched = sum(len(item["surface_matched_concepts"]) for item in items)
        summary["languages"][language] = {
            "case_count": len(items),
            "surface_concept_matches": concept_matched,
            "surface_concept_total": concept_total,
            "surface_concept_coverage": round(concept_matched / concept_total, 4) if concept_total else 0.0,
            "cases_with_prohibited_phrase_hits": sum(bool(item["prohibited_phrase_hits"]) for item in items),
            "cases_with_expected_language_script": sum(item["expected_language_script_present"] is True for item in items),
            "cases_with_sources": sum(int(item["source_count"]) > 0 for item in items),
            "cases_with_allowlisted_primary_sources": sum(int(item.get("trusted_source_count") or 0) > 0 for item in items),
            "cases_with_unattributed_answer_urls": sum(
                int(item.get("unattributed_answer_url_count", 0)) > 0 for item in items
            ),
            "unattributed_answer_url_count": sum(
                int(item.get("unattributed_answer_url_count", 0)) for item in items
            ),
        }
    return summary


AUTOMATED_ACCEPTANCE_VERSION = "bilingual-answer-gate-v2"
_ACCEPTED_REVIEW_STATUSES = {
    "passed_model_assisted",
    "passed_model_assisted_after_repair",
}


def evaluate_automated_acceptance(
    results: list[dict[str, Any]],
    *,
    minimum_concept_coverage: float = 0.5,
    maximum_generation_seconds: float = 180.0,
) -> dict[str, Any]:
    """Apply fail-closed benchmark gates and report a reference-negative false-accept rate.

    The false-accept denominator is restricted to cases whose output contains a
    benchmark-listed prohibited phrase. This is an auditable lexical negative label,
    not a comprehensive semantic gold label or a probability of factual error.
    """
    if not isinstance(results, list) or not results:
        raise ValueError("results must be a non-empty list")
    if (
        isinstance(maximum_generation_seconds, bool)
        or not isinstance(maximum_generation_seconds, (int, float))
        or not math.isfinite(maximum_generation_seconds)
        or maximum_generation_seconds <= 0
    ):
        raise ValueError("maximum_generation_seconds must be positive")
    if (
        isinstance(minimum_concept_coverage, bool)
        or not isinstance(minimum_concept_coverage, (int, float))
        or isinstance(minimum_concept_coverage, bool)
        or not 0 <= minimum_concept_coverage <= 1
    ):
        raise ValueError("minimum_concept_coverage must be between 0 and 1")

    failed_cases = []
    case_decisions = []
    reference_negative_ids = []
    false_acceptance_ids = []
    for item in results:
        if not isinstance(item, dict):
            failed_cases.append({"case_id": None, "failed_gates": ["result_is_object"]})
            case_decisions.append({
                "case_id": None, "accepted": False,
                "reference_negative_by_prohibited_phrase": False,
                "decision": "reject", "failed_gates": ["result_is_object"],
            })
            continue
        gates = {
            "generation_completed": item.get("generation_status") == "completed",
            "generation_within_time_budget": (
                isinstance(item.get("generation_seconds"), (int, float))
                and not isinstance(item.get("generation_seconds"), bool)
                and 0 <= item["generation_seconds"] <= maximum_generation_seconds
            ),
            "surface_diagnostics_completed": item.get("surface_diagnostic_status") == "completed",
            "minimum_reference_concept_coverage": (
                isinstance(item.get("surface_concept_coverage"), (int, float))
                and not isinstance(item.get("surface_concept_coverage"), bool)
                and item["surface_concept_coverage"] >= minimum_concept_coverage
            ),
            "no_prohibited_claims": not bool(item.get("prohibited_phrase_hits")),
            "requested_language_script_present": item.get("expected_language_script_present") is True,
            "at_least_one_retrieved_source": (
                item.get("source_audit_status") == "completed"
                and isinstance(item.get("source_count"), int)
                and not isinstance(item.get("source_count"), bool)
                and item["source_count"] >= 1
            ),
            "at_least_one_allowlisted_primary_source": (
                isinstance(item.get("trusted_source_count"), int)
                and not isinstance(item.get("trusted_source_count"), bool)
                and item["trusted_source_count"] >= 1
            ),
            "claim_review_passed": item.get("research_review_status") in _ACCEPTED_REVIEW_STATUSES,
            "no_unattributed_answer_urls": (
                isinstance(item.get("unattributed_answer_url_count"), int)
                and not isinstance(item.get("unattributed_answer_url_count"), bool)
                and item["unattributed_answer_url_count"] == 0
            ),
        }
        failed = [name for name, passed in gates.items() if not passed]
        case_id = item.get("case_id")
        accepted = not failed
        is_reference_negative = bool(item.get("prohibited_phrase_hits"))
        if failed:
            failed_cases.append({"case_id": case_id, "failed_gates": failed})
        if is_reference_negative:
            reference_negative_ids.append(case_id)
            if accepted:
                false_acceptance_ids.append(case_id)
        case_decisions.append({
            "case_id": case_id,
            "accepted": accepted,
            "reference_negative_by_prohibited_phrase": is_reference_negative,
            "decision": "accept" if accepted else "reject",
            "failed_gates": failed,
        })

    return {
        "version": AUTOMATED_ACCEPTANCE_VERSION,
        "status": "passed" if not failed_cases else "failed",
        "passed": not failed_cases,
        "case_count": len(results),
        "accepted_case_count": len(results) - len(failed_cases),
        "failed_case_count": len(failed_cases),
        "minimum_concept_coverage": float(minimum_concept_coverage),
        "maximum_generation_seconds": float(maximum_generation_seconds),
        "failed_cases": failed_cases,
        "case_decisions": case_decisions,
        "false_acceptance": {
            "reference_negative_definition": "A result with one or more exact prohibited-phrase hits from its versioned reference case.",
            "reference_negative_case_count": len(reference_negative_ids),
            "false_acceptance_count": len(false_acceptance_ids),
            "false_acceptance_rate": (
                round(len(false_acceptance_ids) / len(reference_negative_ids), 4)
                if reference_negative_ids else None
            ),
            "false_acceptance_case_ids": false_acceptance_ids,
            "interpretation": (
                "This rate covers only detected reference-listed prohibited phrases; semantic false "
                "acceptance without a phrase hit remains unmeasured."
            ),
        },
        "human_scoring_used": False,
        "independent_scientific_verification": False,
        "limitations": [
            "Reference-concept matching is lexical and can miss valid paraphrases.",
            "The false-accept denominator includes only exact prohibited-phrase hits.",
            "A passing benchmark does not prove every factual claim is true.",
            "Claim review is model-assisted and must not be described as independent verification.",
        ],
    }
