import json
from pathlib import Path

from app.learning_oracles import evaluate_candidate_claim


FIXTURE = Path(__file__).parent / "fixtures" / "learning_oracles_golden_v1.json"


def test_versioned_oracle_golden_policy_cases():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1
    assert "not a human-adjudicated" in fixture["description"]

    observed = []
    for case in fixture["cases"]:
        result = evaluate_candidate_claim(
            case["content"],
            oracle_id=case["oracle_id"],
            oracle_version=case["oracle_version"],
            claim=case["claim"],
        )
        assert result["status"] == case["expected_status"], case["id"]
        assert result["reason"] == case["expected_reason"], case["id"]
        if case["expected_promoted"] is not None:
            assert result["promoted"] is case["expected_promoted"], case["id"]
        else:
            assert "promoted" not in result, case["id"]
        observed.append((case["id"], result["status"]))

    assert len(observed) == len(fixture["cases"])
    assert len({case_id for case_id, _status in observed}) == len(observed)
