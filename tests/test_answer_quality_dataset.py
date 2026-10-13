import json
from pathlib import Path

import pytest

from app.answer_quality_dataset import DEFAULT_DATASET, load_cases


def test_versioned_dataset_has_both_languages_and_unique_cases():
    cases = load_cases()
    assert len(cases) >= 8
    assert {case["language"] for case in cases} == {"fa", "en"}
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["expected_concepts"] and case["must_not_claim"] for case in cases)


def test_missing_dataset_fails_visibly(tmp_path: Path):
    with pytest.raises(ValueError, match="dataset is missing"):
        load_cases(tmp_path / "absent.jsonl")


def test_empty_dataset_fails_visibly(tmp_path: Path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dataset is empty"):
        load_cases(empty)


def test_duplicate_case_ids_are_rejected(tmp_path: Path):
    one_case = DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0]
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(f"{one_case}\n{one_case}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"duplicate case ID 'fa-evidence-001' at lines 1 and 2"):
        load_cases(duplicate)


def test_malformed_json_fails_with_line_number(tmp_path: Path):
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON.*:1"):
        load_cases(malformed)


def test_non_string_language_fails_cleanly(tmp_path: Path):
    case = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    case["language"] = ["fa"]
    malformed_language = tmp_path / "language.jsonl"
    malformed_language.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported language"):
        load_cases(malformed_language)


def test_non_object_json_line_is_rejected(tmp_path: Path):
    non_object = tmp_path / "non-object.jsonl"
    non_object.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_cases(non_object)


def test_missing_required_field_is_rejected(tmp_path: Path):
    case = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    del case["evidence_policy"]
    incomplete = tmp_path / "incomplete.jsonl"
    incomplete.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields: evidence_policy"):
        load_cases(incomplete)


def test_dataset_with_only_one_language_is_rejected(tmp_path: Path):
    case = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    single_language = tmp_path / "single-language.jsonl"
    single_language.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must include both fa and en"):
        load_cases(single_language)

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_concepts", []),
        ("expected_concepts", [""]),
        ("expected_concepts", ["valid", 7]),
        ("must_not_claim", []),
        ("must_not_claim", ["  "]),
        ("must_not_claim", [None]),
    ],
)
def test_concept_fields_reject_empty_or_non_string_values(tmp_path: Path, field: str, value):
    case = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    case[field] = value
    invalid = tmp_path / "invalid-concepts.jsonl"
    invalid.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=f"field {field} must be a non-empty list of strings"):
        load_cases(invalid)


def test_duplicate_json_object_keys_are_rejected_with_line_number(tmp_path: Path):
    valid = DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0]
    duplicate_key = valid.replace('"id":"fa-evidence-001"', '"id":"shadow","id":"fa-evidence-001"', 1)
    malformed = tmp_path / "duplicate-object-key.jsonl"
    malformed.write_text(duplicate_key + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Invalid JSON.*:1: duplicate object key 'id'"):
        load_cases(malformed)


def test_duplicate_nested_json_object_keys_are_rejected_with_line_number(tmp_path: Path):
    valid = DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0]
    duplicate_nested_key = valid.replace(
        '"expected_concepts":',
        '"metadata":{"source":{"name":"first","name":"shadow"}}, "expected_concepts":',
        1,
    )
    malformed = tmp_path / "duplicate-nested-object-key.jsonl"
    malformed.write_text(duplicate_nested_key + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Invalid JSON.*:1: duplicate object key 'name'"):
        load_cases(malformed)
