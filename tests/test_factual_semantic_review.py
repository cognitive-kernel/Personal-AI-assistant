import json
from pathlib import Path

import pytest

from app.factual_semantic_review import RUBRIC_VERSION, adjudicate_assessment


_FIXTURE = Path(__file__).parent / "fixtures" / "factual_semantic_review_golden.json"


@pytest.mark.parametrize(
    "case",
    json.loads(_FIXTURE.read_text(encoding="utf-8")),
    ids=lambda case: case["id"],
)
def test_golden_semantic_review_policy(case):
    decision = adjudicate_assessment(case["assessment"])
    assert decision.accepted is case["expected"]["accepted"]
    assert decision.score == case["expected"]["score"]
    assert decision.rubric_version == RUBRIC_VERSION
    assert decision.reasons


def test_missing_review_dimension_fails_closed():
    with pytest.raises(ValueError, match="missing boolean rubric checks"):
        adjudicate_assessment({"verdict": "supports", "claim_atomic": True})


def test_unknown_verdict_is_rejected():
    with pytest.raises(ValueError, match="verdict must be"):
        adjudicate_assessment({"verdict": "probably_true"})
