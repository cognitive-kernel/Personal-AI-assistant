"""Deterministic offline quality checks for AI-learning retrieval and answers.

These metrics are regression signals, not proof of factual correctness. They are
intentionally dependency-free so they can run in CI without Ollama or ChatGPT.
"""
from __future__ import annotations

import re
import unicodedata
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


def _normalize_expected_language(value: str) -> str:
    """Normalize the supported language tags used by the offline evaluator."""
    language = str(value or "").strip().casefold().replace("_", "-")
    if language in {"fa", "fa-ir", "persian", "فارسی"} or language.startswith("fa-"):
        return "fa"
    if language in {"en", "en-us", "english"} or language.startswith("en-"):
        return "en"
    raise ValueError("expected_language must identify Persian (fa) or English (en).")


def _language_script_counts(answer: str) -> tuple[int, int]:
    """Count Persian-Arabic and Latin letters in prose, excluding code and URLs."""
    text = str(answer or "")
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwww\.\S+", " ", text, flags=re.IGNORECASE)
    persian_letters = 0
    latin_letters = 0
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "ARABIC" in name:
            persian_letters += 1
        elif "LATIN" in name:
            latin_letters += 1
    return persian_letters, latin_letters


def evaluate_language_adherence(
    answer: str,
    expected_language: str,
    *,
    min_letters: int = 20,
) -> dict[str, Any]:
    """Flag likely wrong-language answers using a deterministic script-ratio heuristic.

    This is a diagnostic regression signal, not semantic language identification.
    Code blocks, inline code, and URLs are excluded so technical examples do not
    dominate the natural-language score.
    """
    expected = _normalize_expected_language(expected_language)
    persian_letters, latin_letters = _language_script_counts(answer)
    total_letters = persian_letters + latin_letters
    expected_letters = persian_letters if expected == "fa" else latin_letters
    ratio = expected_letters / total_letters if total_letters else 0.0
    if total_letters < max(1, int(min_letters)):
        dominant_script = "unknown"
        passed = False
        reason = "insufficient_script_evidence"
    else:
        persian_ratio = persian_letters / total_letters
        latin_ratio = latin_letters / total_letters
        dominant_script = (
            "arabic-script" if persian_ratio >= 0.65
            else "latin-script" if latin_ratio >= 0.65
            else "mixed"
        )
        passed = ratio >= 0.5
        reason = "passed" if passed else "expected_language_not_dominant"
    return {
        "checked": True,
        "passed": passed,
        "expected_language": expected,
        "dominant_script": dominant_script,
        "persian_letter_count": persian_letters,
        "latin_letter_count": latin_letters,
        "expected_language_ratio": round(ratio, 4),
        "reason": reason,
    }


def evaluate_answer(
    answer: str,
    *,
    expected_concepts: Iterable[str] = (),
    must_not_claim: Iterable[str] = (),
    expected_sources: Iterable[str] = (),
    min_chars: int = 20,
    expected_language: str | None = None,
) -> dict[str, Any]:
    """Score answer completeness, rule violations, and optional language adherence.""
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
    language_adherence = (
        evaluate_language_adherence(answer, expected_language)
        if expected_language is not None else None
    )
    return {
        "passed": bool(
            substantive and not violations and concept_recall == 1.0
            and source_coverage == 1.0
            and (language_adherence is None or language_adherence["passed"])
        ),
        "substantive": substantive,
        "answer_chars": len(answer or ""),
        "expected_concept_count": len(concepts),
        "matched_concepts": matched,
        "concept_recall": round(concept_recall, 4),
        "forbidden_claims": violations,
        "expected_source_count": len(sources),
        "matched_sources": found_sources,
        "source_coverage": round(source_coverage, 4),
        "language_adherence": language_adherence,
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
                expected_language=case.get("expected_language"),
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
