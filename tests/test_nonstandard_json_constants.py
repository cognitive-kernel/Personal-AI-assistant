import json
from pathlib import Path

import pytest

from app.answer_quality_dataset import DEFAULT_DATASET, load_cases

@pytest.mark.parametrize(("constant", "value"), [
    ("NaN", float("nan")),
    ("Infinity", float("inf")),
    ("-Infinity", float("-inf")),
])
def test_nonstandard_constants_in_required_prompt_are_rejected(tmp_path: Path, constant: str, value: float):
    case = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0])
    case["prompt"] = value
    malformed = tmp_path / "nonstandard-required-field.jsonl"
    malformed.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=rf"Invalid JSON.*:1: non-standard JSON constant '{constant}'"):
        load_cases(malformed)
