"""Versioned, curated reference checks for a small set of exact technical claims.

A match means only that a reviewed canonical claim is present verbatim in a
candidate and agrees with this registry. It is not semantic entailment, proof
that the surrounding lesson is correct, or permission to promote knowledge.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


ORACLE_CATALOG_VERSION = 1
ORACLE_CATALOG_STATUS = "provisional_requires_independent_human_review"

_ORACLES: dict[tuple[str, int], dict[str, Any]] = {
    ("python.json-decode-error", 1): {
        "oracle_id": "python.json-decode-error",
        "version": 1,
        "domain": "python",
        "canonical_claims": [
            "Python's json.loads raises JSONDecodeError when the input is not a valid JSON document.",
            "تابع json.loads در پایتون هنگام دریافت متن JSON نامعتبر، خطای JSONDecodeError ایجاد می‌کند.",
        ],
        "source_url": "https://docs.python.org/3/library/json.html",
        "source_summary": "The Python json documentation describes JSONDecodeError for invalid JSON input.",
    },
    ("python.dict-get-default", 1): {
        "oracle_id": "python.dict-get-default",
        "version": 1,
        "domain": "python",
        "canonical_claims": [
            "Python dict.get returns the default value when the key is absent.",
            "متد dict.get در پایتون وقتی کلید وجود ندارد، مقدار پیش‌فرض را برمی‌گرداند.",
        ],
        "source_url": "https://docs.python.org/3/library/stdtypes.html#dict.get",
        "source_summary": "The Python mapping documentation describes dict.get and its default argument.",
    },
    ("sqlite.foreign-key-check", 1): {
        "oracle_id": "sqlite.foreign-key-check",
        "version": 1,
        "domain": "sqlite",
        "canonical_claims": [
            "SQLite PRAGMA foreign_key_check reports foreign-key violations.",
            "دستور PRAGMA foreign_key_check در SQLite موارد نقض کلید خارجی را گزارش می‌کند.",
        ],
        "source_url": "https://www.sqlite.org/pragma.html#pragma_foreign_key_check",
        "source_summary": "The SQLite PRAGMA documentation describes foreign_key_check output for foreign-key violations.",
    },
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def list_oracles() -> list[dict[str, Any]]:
    """Return public metadata only; no source secrets or mutable registry objects."""
    items: list[dict[str, Any]] = []
    for (oracle_id, version), oracle in sorted(_ORACLES.items()):
        items.append({
            "oracle_id": oracle_id,
            "version": version,
            "catalog_status": ORACLE_CATALOG_STATUS,
            "domain": oracle["domain"],
            "canonical_claims": list(oracle["canonical_claims"]),
            "source_url": oracle["source_url"],
            "source_summary": oracle["source_summary"],
            "source_summary_sha256": hashlib.sha256(
                oracle["source_summary"].encode("utf-8")
            ).hexdigest(),
        })
    return items


def evaluate_candidate_claim(
    content: str, *, oracle_id: str, oracle_version: int, claim: str
) -> dict[str, Any]:
    """Fail closed unless a versioned canonical claim appears in candidate text."""
    if not isinstance(content, str) or not isinstance(claim, str) or not claim.strip():
        return {"status": "inconclusive", "reason": "invalid_claim_input"}
    if not isinstance(oracle_id, str) or not oracle_id.strip():
        return {"status": "inconclusive", "reason": "invalid_oracle_id"}
    if isinstance(oracle_version, bool) or not isinstance(oracle_version, int) or oracle_version < 1:
        return {"status": "inconclusive", "reason": "invalid_oracle_version"}
    oracle = _ORACLES.get((oracle_id, oracle_version))
    if oracle is None:
        return {"status": "inconclusive", "reason": "unknown_oracle_version"}
    normalized_claim = _normalize(claim)
    canonical = next(
        (item for item in oracle["canonical_claims"] if _normalize(item) == normalized_claim),
        None,
    )
    if canonical is None:
        return {
            "status": "inconclusive",
            "reason": "claim_not_in_curated_reference_set",
            "oracle_id": oracle_id,
            "oracle_version": oracle_version,
        }
    if normalized_claim not in _normalize(content):
        return {
            "status": "inconclusive",
            "reason": "claim_not_present_in_candidate",
            "oracle_id": oracle_id,
            "oracle_version": oracle_version,
        }
    summary = str(oracle["source_summary"])
    return {
        "status": "matched",
        "catalog_status": ORACLE_CATALOG_STATUS,
        "reason": "exact_curated_claim_present",
        "oracle_id": oracle_id,
        "oracle_version": oracle_version,
        "domain": oracle["domain"],
        "canonical_claim": canonical,
        "source_url": oracle["source_url"],
        "source_summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        "promoted": False,
        "limitation": (
            "Exact canonical-text match only; does not establish semantic entailment, "
            "exclude contradictory surrounding text, or promote knowledge."
        ),
    }
