"""Conservative surface-level checks for bilingual answer-quality runs.

These checks are diagnostics only. They are not semantic adjudication or a model-quality score.
"""
from __future__ import annotations

import re
from typing import Any


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

    # URLs written by the model are not treated as retrieved evidence unless
    # the exact URL also appears in the structured source metadata.
    answer_urls = []
    for match in re.findall(r"https://[^\\s<>\\]\\\"']+", answer):
        url = match.rstrip(".,;:!?)]}\\\"'")
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
        "Not a model-quality score. Human adjudication and a pinned live-model run are required."
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
            "cases_with_unattributed_answer_urls": sum(
                int(item.get("unattributed_answer_url_count", 0)) > 0 for item in items
            ),
            "unattributed_answer_url_count": sum(
                int(item.get("unattributed_answer_url_count", 0)) for item in items
            ),
        }
    return summary
