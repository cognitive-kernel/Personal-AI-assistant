"""Deterministic offline quality checks for AI-learning retrieval and answers.

These metrics are regression signals, not proof of factual correctness. They are
intentionally dependency-free so they can run in CI without Ollama or ChatGPT.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any


def normalize_text(value: Any) -> str:
    """Normalize Persian/Arabic letter variants, diacritics, punctuation and spacing."""
    text = str(value or "").casefold()
    text = text.translate(str.maketrans({
        "ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه",
        "أ": "ا", "إ": "ا", "ٱ": "ا",
    }))
    text = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", text)
    text = re.sub(r"[^\w\u200c]+", " ", text, flags=re.UNICODE)
    return " ".join(text.replace("\u200c", " ").split())


def evaluate_answer(
    answer: str,
    *,
    expected_concepts: Iterable[str] = (),
    must_not_claim: Iterable[str] = (),
    expected_sources: Iterable[str] = (),
    min_chars: int = 20,
) -> dict[str, Any]:
    """Score answer completeness and rule violations using explicit test expectations."""
    normalized = normalize_text(answer)
    concepts = [normalize_text(item) for item in expected_concepts if normalize_text(item)]
    forbidden = [normalize_text(item) for item in must_not_claim if normalize_text(item)]
    sources = [str(item).strip() for item in expected_sources if str(item).strip()]
    matched = [item for item in concepts if item in normalized]
    violations = [item for item in forbidden if item in normalized]
    found_sources = [url for url in sources if url in (answer or "")]
    concept_recall = len(matched) / len(concepts) if concepts else 1.0
    source_coverage = len(found_sources) / len(sources) if sources else 1.0
    substantive = len(normalized) >= max(0, int(min_chars))
    return {
        "passed": bool(substantive and not violations and concept_recall == 1.0 and source_coverage == 1.0),
        "substantive": substantive,
        "answer_chars": len(answer or ""),
        "expected_concept_count": len(concepts),
        "matched_concepts": matched,
        "concept_recall": round(concept_recall, 4),
        "forbidden_claims": violations,
        "expected_source_count": len(sources),
        "matched_sources": found_sources,
        "source_coverage": round(source_coverage, 4),
    }


def evaluate_retrieval_case(
    retrieved: Iterable[Mapping[str, Any]],
    *,
    expected_ids: Iterable[int] = (),
    forbidden_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Evaluate expected hits and enforce that explicitly forbidden IDs never leak."""
    items = list(retrieved)
    returned_ids = []
    for item in items:
        value = item.get("chunk_id", item.get("id"))
        if isinstance(value, int) and not isinstance(value, bool):
            returned_ids.append(value)
    expected = set(expected_ids)
    forbidden = set(forbidden_ids)
    returned = set(returned_ids)
    hits = expected & returned
    leaked = forbidden & returned
    recall = len(hits) / len(expected) if expected else 1.0
    precision = len(hits) / len(returned) if returned else (1.0 if not expected else 0.0)
    return {
        "passed": not leaked and recall == 1.0,
        "returned_ids": returned_ids,
        "expected_ids": sorted(expected),
        "hits": sorted(hits),
        "missing_ids": sorted(expected - returned),
        "forbidden_ids_found": sorted(leaked),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "result_count": len(items),
    }


def evaluate_dataset(
    cases: Iterable[Mapping[str, Any]],
    *,
    retrieve_fn: Callable[[str], Iterable[Mapping[str, Any]]],
    answer_fn: Callable[[str, list[Mapping[str, Any]]], str] | None = None,
) -> dict[str, Any]:
    """Run a small deterministic retrieval/answer benchmark over caller-supplied cases."""
    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("id", f"case-{len(results) + 1}"))
        query = str(case.get("query", ""))
        retrieved = list(retrieve_fn(query))
        retrieval = evaluate_retrieval_case(
            retrieved,
            expected_ids=case.get("expected_chunk_ids", ()),
            forbidden_ids=case.get("forbidden_chunk_ids", ()),
        )
        result: dict[str, Any] = {"id": case_id, "retrieval": retrieval}
        if answer_fn is not None:
            answer = answer_fn(query, retrieved)
            result["answer"] = evaluate_answer(
                answer,
                expected_concepts=case.get("expected_concepts", ()),
                must_not_claim=case.get("must_not_claim", ()),
                expected_sources=case.get("expected_sources", ()),
                min_chars=int(case.get("min_answer_chars", 20)),
            )
        result["passed"] = retrieval["passed"] and result.get("answer", {}).get("passed", True)
        results.append(result)
    count = len(results)
    return {
        "case_count": count,
        "passed_count": sum(bool(item["passed"]) for item in results),
        "failed_count": sum(not bool(item["passed"]) for item in results),
        "pass_rate": round(sum(bool(item["passed"]) for item in results) / count, 4) if count else 1.0,
        "results": results,
    }
