"""Fail-closed mapping from lesson claims to explicit automated tests."""
from __future__ import annotations

import re
from typing import Any

CLAIM_TEST_COVERAGE_VERSION = "claim-test-coverage-v1"


def extract_candidate_claims(content: str, candidate_id: int) -> list[dict[str, str]]:
    """Extract stable claim IDs using the same conservative sentence boundaries as review.

    IDs are positional within the immutable candidate content hash. They identify
    review units, not a semantic guarantee that every grammatical statement was found.
    """
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
        raise ValueError("candidate_id must be a positive integer")
    sentences = [
        re.sub(r"\s+", " ", part).strip(" \t\r\n•-*")
        for part in re.split(r"(?<=[.!?؟])\s+|\n+", content)
    ]
    sentences = [sentence for sentence in sentences if len(sentence) >= 24][:100]
    return [
        {"claim_id": f"{candidate_id}:{index}", "text": sentence}
        for index, sentence in enumerate(sentences, start=1)
    ]


def evaluate_claim_test_coverage(
    claim_ids: list[str],
    test_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify explicit structural coverage; do not infer that a test is semantically adequate.

    A complete mapping requires at least one candidate claim, at least one test,
    unique test names, known claim IDs, and at least one declared test per claim.
    This is a necessary condition for promotion, not proof that a test truly tests
    its assigned claim; trusted oracle validation remains a separate requirement.
    """
    issues: list[str] = []
    valid_claim_ids: list[str] = []
    if not isinstance(claim_ids, list) or not claim_ids:
        issues.append("candidate_has_no_extractable_claims")
    else:
        for claim_id in claim_ids:
            if not isinstance(claim_id, str) or not claim_id.strip():
                issues.append("claim_ids_must_be_nonempty_strings")
                continue
            normalized = claim_id.strip()
            if normalized in valid_claim_ids:
                issues.append("duplicate_candidate_claim_id")
            else:
                valid_claim_ids.append(normalized)

    if not isinstance(test_plan, list) or not test_plan:
        issues.append("test_plan_is_empty_or_invalid")
        tests: list[dict[str, Any]] = []
    else:
        tests = test_plan

    names: list[str] = []
    mapping: dict[str, list[str]] = {claim_id: [] for claim_id in valid_claim_ids}
    unknown_claim_ids: list[str] = []
    for item in tests:
        if not isinstance(item, dict):
            issues.append("test_plan_item_is_not_an_object")
            continue
        name = item.get("test")
        if not isinstance(name, str) or not name.strip():
            issues.append("test_name_missing")
            name = ""
        else:
            name = name.strip()
            names.append(name)
        assigned = item.get("claim_ids")
        if not isinstance(assigned, list) or not assigned:
            issues.append("test_has_no_explicit_claim_mapping")
            continue
        local_seen: set[str] = set()
        for claim_id in assigned:
            if not isinstance(claim_id, str) or not claim_id.strip():
                issues.append("mapped_claim_id_must_be_nonempty_string")
                continue
            claim_id = claim_id.strip()
            if claim_id in local_seen:
                issues.append("duplicate_claim_id_within_test")
                continue
            local_seen.add(claim_id)
            if claim_id not in mapping:
                unknown_claim_ids.append(claim_id)
                issues.append("test_maps_to_unknown_claim")
                continue
            if name and name not in mapping[claim_id]:
                mapping[claim_id].append(name)

    if len(names) != len(set(names)):
        issues.append("duplicate_test_names")
    missing = [claim_id for claim_id, assigned_tests in mapping.items() if not assigned_tests]
    if missing:
        issues.append("one_or_more_claims_have_no_tests")
    covered = len(valid_claim_ids) - len(missing)
    total = len(valid_claim_ids)
    issues = sorted(set(issues))
    unknown_claim_ids = sorted(set(unknown_claim_ids))
    return {
        "version": CLAIM_TEST_COVERAGE_VERSION,
        "complete": bool(total > 0 and not issues and not missing and len(tests) > 0),
        "candidate_claim_count": total,
        "covered_claim_count": covered,
        "coverage_ratio": round(covered / total, 4) if total else 0.0,
        "claim_ids": valid_claim_ids,
        "covered_claim_ids": [claim_id for claim_id in valid_claim_ids if mapping.get(claim_id)],
        "missing_claim_ids": missing,
        "unknown_claim_ids": unknown_claim_ids,
        "claim_to_tests": mapping,
        "issues": issues,
        "mapping_is_explicit": (
            bool(tests)
            and "test_has_no_explicit_claim_mapping" not in issues
            and "mapped_claim_id_must_be_nonempty_string" not in issues
        ),
        "semantic_test_adequacy_independently_verified": False,
        "interpretation": (
            "A complete mapping is a structural prerequisite only. Test-to-claim relevance "
            "must still be reviewed; oracle trust and exact runner outputs are checked separately."
        ),
    }
