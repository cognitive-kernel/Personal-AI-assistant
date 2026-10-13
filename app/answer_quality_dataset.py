"""Validation helpers for the versioned bilingual answer-quality benchmark dataset."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASET_VERSION = "v1"
DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "benchmarks" / "answer_quality" / DATASET_VERSION / "cases.jsonl"
REQUIRED_FIELDS = {"id", "language", "prompt", "expected_concepts", "evidence_policy", "must_not_claim"}
SUPPORTED_LANGUAGES = {"fa", "en"}


class _DuplicateJSONKeyError(ValueError):
    """Raised when a JSON object repeats a key instead of defining it once."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def load_cases(path: str | Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    """Load and validate all cases; fail closed on absent or malformed benchmark data."""
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise ValueError(f"Answer-quality dataset is missing: {dataset_path}")

    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line, object_pairs_hook=_reject_duplicate_json_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at {dataset_path}:{line_number}: {exc.msg}") from exc
        except _DuplicateJSONKeyError as exc:
            raise ValueError(f"Invalid JSON at {dataset_path}:{line_number}: {exc}") from exc
        if not isinstance(case, dict):
            raise ValueError(f"Case at line {line_number} must be a JSON object")
        missing = REQUIRED_FIELDS - case.keys()
        if missing:
            raise ValueError(f"Case at line {line_number} missing fields: {', '.join(sorted(missing))}")
        if not isinstance(case["id"], str) or not case["id"].strip():
            raise ValueError(f"Case at line {line_number} has an empty/non-string id")
        language = case["language"]
        if not isinstance(language, str) or language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Case {case['id']} has unsupported language: {language!r}")
        if not isinstance(case["prompt"], str) or not case["prompt"].strip():
            raise ValueError(f"Case {case['id']} has an empty/non-string prompt")
        for field in ("expected_concepts", "must_not_claim"):
            values = case[field]
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"Case {case['id']} field {field} must be a non-empty list of strings")
        if not isinstance(case["evidence_policy"], str) or not case["evidence_policy"].strip():
            raise ValueError(f"Case {case['id']} has an empty/non-string evidence_policy")
        cases.append(case)

    if not cases:
        raise ValueError(f"Answer-quality dataset is empty: {dataset_path}")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Answer-quality dataset contains duplicate case IDs")
    languages = {case["language"] for case in cases}
    if languages != SUPPORTED_LANGUAGES:
        raise ValueError(f"Dataset must include both fa and en cases; found: {sorted(languages)}")
    return cases
