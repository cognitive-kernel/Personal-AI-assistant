"""Deterministic policy for adjudicating semantic factual-review assessments.

This module does not infer truth from prose or trust an LLM verdict. It validates
an explicitly recorded assessment against fixed criteria and abstains when the
assessment is incomplete, out of scope, or version-incompatible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RUBRIC_VERSION = "factual-semantic-rubric-v1"
VERDICTS = {"supports", "contradicts", "insufficient_evidence", "conflict"}
_REQUIRED_CHECKS = (
    "claim_atomic",
    "source_directly_addresses_claim",
    "scope_matches",
    "source_version_matches_claim",
    "runtime_environment_matches",
    "source_is_authoritative",
    "no_unresolved_conflict",
    "reviewer_is_independent",
)


@dataclass(frozen=True)
class ReviewDecision:
    verdict: str
    accepted: bool
    score: float
    rubric_version: str
    reasons: tuple[str, ...]


def adjudicate_assessment(assessment: dict[str, Any]) -> ReviewDecision:
    """Apply a fail-closed rubric to a structured semantic-review assessment.

    The booleans are evidence supplied by a reviewer/evaluator, not facts proven
    by this function. The function enforces completeness and policy consistently.
    """
    if not isinstance(assessment, dict):
        raise ValueError("assessment must be an object")
    verdict = assessment.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError("verdict must be supports, contradicts, insufficient_evidence, or conflict")
    missing = [key for key in _REQUIRED_CHECKS if not isinstance(assessment.get(key), bool)]
    if missing:
        raise ValueError("missing boolean rubric checks: " + ", ".join(missing))

    passed = sum(assessment[key] is True for key in _REQUIRED_CHECKS)
    score = round(passed / len(_REQUIRED_CHECKS), 4)
    reasons: list[str] = []
    failed = [key for key in _REQUIRED_CHECKS if assessment[key] is not True]
    if failed:
        reasons.append("failed_checks:" + ",".join(failed))
    if verdict != "supports":
        reasons.append("verdict_not_supports:" + verdict)
    accepted = verdict == "supports" and not failed and score == 1.0
    if accepted:
        reasons.append("all_required_checks_passed")
    else:
        reasons.append("fail_closed_no_promotion")
    return ReviewDecision(
        verdict=verdict,
        accepted=accepted,
        score=score,
        rubric_version=RUBRIC_VERSION,
        reasons=tuple(reasons),
    )
