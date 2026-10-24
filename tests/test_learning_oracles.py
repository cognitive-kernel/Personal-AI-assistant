from app.learning_oracles import (
    ORACLE_CATALOG_VERSION,
    ORACLE_CATALOG_STATUS,
    evaluate_candidate_claim,
    list_oracles,
)


def test_oracle_catalog_is_versioned_and_explicitly_provisional():
    items = list_oracles()
    assert ORACLE_CATALOG_VERSION == 1
    assert len(items) >= 3
    assert all(item["catalog_status"] == ORACLE_CATALOG_STATUS for item in items)
    assert all(item["source_url"].startswith("https://") for item in items)
    assert all(len(item["source_summary_sha256"]) == 64 for item in items)


def test_exact_curated_claim_matches_but_never_promotes_knowledge():
    claim = "Python dict.get returns the default value when the key is absent."
    result = evaluate_candidate_claim(
        f"Explanation. {claim} Additional context.",
        oracle_id="python.dict-get-default",
        oracle_version=1,
        claim=claim,
    )
    assert result["status"] == "matched"
    assert result["promoted"] is False
    assert "does not establish semantic entailment" in result["limitation"]


def test_unknown_version_and_noncanonical_claim_fail_closed():
    unknown = evaluate_candidate_claim(
        "Some lesson", oracle_id="python.dict-get-default", oracle_version=999,
        claim="Python dict.get returns the default value when the key is absent.",
    )
    unsupported = evaluate_candidate_claim(
        "Some lesson", oracle_id="python.dict-get-default", oracle_version=1,
        claim="dict.get always returns None.",
    )
    assert unknown["status"] == "inconclusive"
    assert unknown["reason"] == "unknown_oracle_version"
    assert unsupported["status"] == "inconclusive"
    assert unsupported["reason"] == "claim_not_in_curated_reference_set"


def test_claim_absent_from_candidate_fails_closed():
    claim = "SQLite PRAGMA foreign_key_check reports foreign-key violations."
    result = evaluate_candidate_claim(
        "This lesson discusses SQLite without making the canonical claim.",
        oracle_id="sqlite.foreign-key-check",
        oracle_version=1,
        claim=claim,
    )
    assert result["status"] == "inconclusive"
    assert result["reason"] == "claim_not_present_in_candidate"


def test_boolean_or_invalid_version_is_rejected():
    result = evaluate_candidate_claim(
        "Python's json.loads raises JSONDecodeError when the input is not a valid JSON document.",
        oracle_id="python.json-decode-error",
        oracle_version=True,
        claim="Python's json.loads raises JSONDecodeError when the input is not a valid JSON document.",
    )
    assert result["status"] == "inconclusive"
    assert result["reason"] == "invalid_oracle_version"
