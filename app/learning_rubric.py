"""Versioned, deterministic scoring for explicit concept rubrics.

A concept match is evidence that an answer contains a rubric concept, not proof of
understanding. This scorer never asks an LLM to award points at submission time.
"""
from __future__ import annotations

import re
from typing import Any

RUBRIC_VERSION = "concept-coverage-v1"


def normalize_concept_text(value: str) -> str:
    """Normalize common Persian/Arabic variants and punctuation for matching."""
    value = value.casefold().replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def validate_rubric(rubric: Any, question_count: int) -> bool:
    """Accept only a small, bounded, schema-valid rubric; reject ambiguous shapes."""
    if not isinstance(rubric, list) or len(rubric) != question_count or not 1 <= question_count <= 3:
        return False
    for question in rubric:
        if not isinstance(question, dict):
            return False
        criteria = question.get("criteria")
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 5:
            return False
        seen_ids: set[str] = set()
        for criterion in criteria:
            if not isinstance(criterion, dict):
                return False
            criterion_id = criterion.get("id")
            weight = criterion.get("weight", 1)
            variants = criterion.get("accepted_phrases")
            if (
                not isinstance(criterion_id, str) or not criterion_id.strip()
                or criterion_id in seen_ids or len(criterion_id) > 80
                or not isinstance(weight, (int, float)) or isinstance(weight, bool)
                or not 0 < weight <= 10
                or not isinstance(variants, list) or not 1 <= len(variants) <= 6
                or any(
                    not isinstance(item, str) or len(normalize_concept_text(item)) < 3 or len(item) > 160
                    for item in variants
                )
            ):
                return False
            seen_ids.add(criterion_id)
    return True




def _phrase_occurrences(padded_answer: str, padded_phrase: str) -> list[int]:
    """Return starts of whole-phrase occurrences in normalized answer text."""
    starts: list[int] = []
    start = 0
    while True:
        found = padded_answer.find(padded_phrase, start)
        if found < 0:
            return starts
        starts.append(found)
        start = found + 1


def _is_negated_occurrence(padded_answer: str, padded_phrase: str, phrase_start: int) -> bool:
    """Conservatively reject a concept phrase immediately preceded by common negation."""
    prefix = padded_answer[:phrase_start].rstrip()
    return bool(re.search(
        r"(?:^|\\s)(?:not|no|never|without|false|incorrect|isn't|doesn't|cannot|"
        r"نیست|نبود|بدون|نه|هرگز|عدم)\\s*$",
        prefix,
        flags=re.IGNORECASE,
    ))

def score_with_rubric(answers: list[str], rubric: list[dict[str, Any]]) -> dict[str, Any]:
    """Score required concepts by weighted accepted-phrase coverage, deterministically."""
    if not isinstance(answers, list) or len(answers) != len(rubric):
        raise ValueError("Answer count must match the rubric question count.")
    if not validate_rubric(rubric, len(answers)):
        raise ValueError("Saved rubric is invalid.")
    per_question: list[dict[str, Any]] = []
    total_weight = 0.0
    earned_weight = 0.0
    for index, (answer, item) in enumerate(zip(answers, rubric), start=1):
        if not isinstance(answer, str):
            raise ValueError("Every answer must be text.")
        normalized_answer = normalize_concept_text(answer)
        criteria_results = []
        question_total = 0.0
        question_earned = 0.0
        for criterion in item["criteria"]:
            weight = float(criterion.get("weight", 1))
            question_total += weight
            total_weight += weight
            variants = [normalize_concept_text(value) for value in criterion["accepted_phrases"]]
            padded_answer = f" {normalized_answer} "
            matched = any(
                variant
                and any(
                    not _is_negated_occurrence(padded_answer, variant, match_start)
                    for match_start in _phrase_occurrences(padded_answer, f" {variant} ")
                )
                for variant in variants
            )
            if matched:
                question_earned += weight
                earned_weight += weight
            criteria_results.append({
                "criterion_id": criterion["id"],
                "matched": matched,
                "weight": weight,
            })
        per_question.append({
            "question_index": index,
            "score": round(100 * question_earned / question_total, 2) if question_total else 0.0,
            "matched_criteria": sum(1 for criterion in criteria_results if criterion["matched"]),
            "criterion_count": len(criteria_results),
            "criteria": criteria_results,
        })
    score = round(100 * earned_weight / total_weight, 2) if total_weight else 0.0
    return {
        "rubric_version": RUBRIC_VERSION,
        "score": score,
        "correct_count": sum(1 for item in per_question if item["score"] == 100.0),
        "question_count": len(per_question),
        "per_question": per_question,
        "scoring_method": "rubric_concept_coverage_v1",
        "independent_verification": False,
        "note": "امتیاز بر اساس پوشش معیارهای مفهومیِ تعریف‌شده محاسبه شده است؛ تطبیق معیارها به‌تنهایی اثبات قطعی فهم یا صحت علمی نیست.",
    }
