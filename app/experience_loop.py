"""Bounded, deterministic verification for reusable learning experiences.

This module never executes model-generated code or shell commands. It runs a small
allowlisted set of checks against a generated learning artifact and source excerpts.
A verified record means these checks passed; it is not a claim of independent
scientific fact-checking.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import experience


RUNNER_VERSION = "experience-loop-v1"


def _source_evidence(retrieved_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in retrieved_sources:
        url = source.get("url")
        content = source.get("content")
        title = source.get("title", "")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        if not isinstance(content, str) or len(content.strip()) < 80 or url in seen:
            continue
        excerpt = content.strip()[:400]
        seen.add(url)
        evidence.append({
            "url": url,
            "title": str(title)[:300],
            "claim": "Source provenance recorded; semantic support for lesson claims has not been independently established.",
            "excerpt": excerpt,
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "original_language": "preserved",
        })
    return evidence[:10]


def verify_learning_artifact(
    *,
    topic: str,
    lesson: str,
    assessment: dict[str, Any],
    quality: dict[str, Any],
    retrieved_sources: list[dict[str, Any]],
    job_id: int,
) -> dict[str, Any]:
    """Run deterministic checks and persist a bounded, auditable experience."""
    evidence = _source_evidence(retrieved_sources)
    outcomes = assessment.get("learning_outcomes", [])
    questions = assessment.get("questions", [])
    answers = assessment.get("answer_key", [])

    checks = [
        {
            "test": "lesson_quality_gate",
            "passed": quality.get("accepted") is True
            and quality.get("word_count", 0) >= quality.get("target_word_count", 1),
            "actual": {
                "accepted": quality.get("accepted"),
                "word_count": quality.get("word_count"),
                "target_word_count": quality.get("target_word_count"),
                "score": quality.get("score"),
            },
        },
        {
            "test": "assessment_contract",
            "passed": len(outcomes) == 3 and len(questions) == 3 and len(answers) == 3
            and all(isinstance(value, str) and value.strip() for value in [*outcomes, *questions, *answers]),
            "actual": {
                "outcomes": len(outcomes) if isinstance(outcomes, list) else None,
                "questions": len(questions) if isinstance(questions, list) else None,
                "answers": len(answers) if isinstance(answers, list) else None,
            },
        },
        {
            "test": "source_excerpt_integrity",
            "passed": bool(evidence) and all(
                hashlib.sha256(item["excerpt"].encode("utf-8")).hexdigest() == item["excerpt_sha256"]
                and any(
                    source.get("url") == item["url"]
                    and isinstance(source.get("content"), str)
                    and item["excerpt"] in source["content"]
                    for source in retrieved_sources
                )
                for item in evidence
            ),
            "actual": {"source_count": len(evidence), "excerpt_hashes_checked": len(evidence)},
        },
    ]
    all_passed = all(item["passed"] for item in checks)
    problem = (
        f"Learning job #{job_id} artifact verification: "
        + ", ".join(f"{item['test']}={'passed' if item['passed'] else 'failed'}" for item in checks)
    )
    takeaway = (
        f"Reusable lesson artifact for topic: {topic}\n\n"
        + lesson[:6000]
        + "\n\nVerification scope: deterministic quality, assessment-contract, and source-excerpt-integrity checks only. "
        "These checks do not independently establish factual correctness of lesson claims."
    )
    record = experience.create_experience(
        topic=topic,
        problem=problem,
        hypothesis="A lesson is reusable only after bounded deterministic quality, contract, and source-provenance checks pass.",
    )
    status = "verified" if all_passed else "needs_review"
    saved = experience.update_experience(
        record["id"],
        status=status,
        source_evidence=evidence,
        test_plan=[
            {"test": item["test"], "runner": RUNNER_VERSION, "scope": "deterministic artifact check"}
            for item in checks
        ],
        test_results=[
            {**item, "runner": RUNNER_VERSION, "job_id": job_id}
            for item in checks
        ],
        takeaway=takeaway,
        _controlled_run=True,
    )
    return {
        "experience_id": saved["id"],
        "status": saved["status"],
        "runner_version": RUNNER_VERSION,
        "checks": checks,
        "source_evidence_count": len(evidence),
        "verification_scope": "artifact quality and provenance; not independent factual verification",
    }
