"""Deterministic concept-coverage scoring for structured learning rubrics.

This improves over exact-string matching but is still lexical, not a semantic
judge. Rubrics are generated with the lesson and must be evaluated against
versioned benchmark cases before being trusted for certification.
"""
from __future__ import annotations

import re
from typing import Any


def normalize_answer_text(value: str) -> str:
    text = value.casefold().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_rubric(value: Any, question_count: int) -> list[dict[str, Any]] | None:
    """Validate model-generated rubric structure; return None on any ambiguity."""
    if not isinstance(value, list) or len(value) != question_count or not 1 <= question_count <= 3:
        return None
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        concepts = item.get("required_concepts")
        if not isinstance(concepts, list) or not 2 <= len(concepts) <= 6:
            return None
        normalized_concepts = []
        for concept_item in concepts:
            if not isinstance(concept_item, dict):
                return None
            concept = concept_item.get("concept")
            phrases = concept_item.get("accepted_phrases")
            if (
                not isinstance(concept, str) or not 2 <= len(concept.strip()) <= 120
                or not isinstance(phrases, list) or not 1 <= len(phrases) <= 6
                or any(not isinstance(phrase, str) or not 2 <= len(phrase.strip()) <= 160 for phrase in phrases)
            ):
                return None
            clean_phrases = []
            seen: set[str] = set()
            for phrase in [concept, *phrases]:
                clean = phrase.strip()
                key = normalize_answer_text(clean)
                if key and key not in seen:
                    seen.add(key)
                    clean_phrases.append(clean)
            normalized_concepts.append({
                "concept": concept.strip(),
                "accepted_phrases": clean_phrases,
            })
        normalized.append({"required_concepts": normalized_concepts})
    return normalized


def score_answers(answers: list[str], rubric: list[dict[str, Any]]) -> dict[str, Any]:
    """Score required concept coverage with explicit per-question diagnostics."""
    if not isinstance(answers, list) or not isinstance(rubric, list) or len(answers) != len(rubric):
        raise ValueError("answers and rubric must be lists with equal lengths")
    details = []
    matched_total = 0
    concept_total = 0
    correct_count = 0
    for index, (answer, item) in enumerate(zip(answers, rubric, strict=True), start=1):
        if not isinstance(answer, str) or not isinstance(item, dict):
            raise ValueError("answers and rubric entries have invalid types")
        normalized_answer = normalize_answer_text(answer)
        concepts = item.get("required_concepts")
        if not isinstance(concepts, list) or not concepts:
            raise ValueError("each rubric question must contain required concepts")
        matched = []
        missing = []
        for concept in concepts:
            if not isinstance(concept, dict) or not isinstance(concept.get("accepted_phrases"), list):
                raise ValueError("rubric concept is malformed")
            phrases = [
                normalize_answer_text(phrase)
                for phrase in concept["accepted_phrases"]
                if isinstance(phrase, str) and normalize_answer_text(phrase)
            ]
            label = str(concept.get("concept", ""))[:120]
            padded_answer = f" {normalized_answer} "
            if any(f" {phrase} " in padded_answer for phrase in phrases):
                matched.append(label)
            else:
                missing.append(label)
        total = len(concepts)
        coverage = len(matched) / total
        matched_total += len(matched)
        concept_total += total
        passed = coverage >= 0.75
        correct_count += int(passed)
        details.append({
            "question_index": index,
            "matched_concepts": matched,
            "missing_concepts": missing,
            "concept_coverage": round(coverage, 4),
            "passed": passed,
        })
    return {
        "score": round(100 * matched_total / concept_total, 2) if concept_total else 0.0,
        "correct_count": correct_count,
        "question_count": len(answers),
        "scoring_method": "rubric_concept_coverage_v1",
        "question_results": details,
        "independent_verification": False,
        "note": "نمره بر اساس پوشش واژه‌ای مفاهیم rubric است؛ ارزیابی معنایی مستقل یا اثبات تسلط نیست.",
    }
