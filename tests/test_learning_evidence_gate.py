import json
from pathlib import Path

from app.learning_evidence_gate import evaluate_lesson_evidence


def _source(url, text="This source excerpt contains enough characters for evidence."):
    return {"url": url, "content": text}


def _review(urls):
    return {
        "passed": True,
        "overall": "supported",
        "claims": [{
            "claim": "A factual claim",
            "verdict": "supported",
            "evidence_urls": urls,
            "evidence_quotes": [{"url": urls[0], "quote": "This source excerpt contains enough characters for evidence."}],
            "reason": "Directly supported by excerpts",
        }],
        "source_conflicts": [],
    }


def test_evidence_gate_passes_only_with_two_cited_hosts_and_supported_claims():
    urls = ["https://docs.python.org/3/tutorial/index.html", "https://learn.microsoft.com/en-us/sql/"]
    lesson = "Lesson content. Sources: " + " ".join(urls)
    result = evaluate_lesson_evidence(lesson, [_source(url) for url in urls], _review(urls))

    assert result["accepted"] is True
    assert result["status"] == "evidence_gate_passed_not_proof_of_truth"
    assert result["independent_scientific_verification"] is False
    assert result["claim_count"] == 1
    assert result["explicitly_mapped_claim_count"] == 1
    assert result["failed_gates"] == []


def test_evidence_gate_fails_closed_without_multiple_domains():
    urls = ["https://docs.example.org/a", "https://docs.example.org/b"]
    result = evaluate_lesson_evidence(
        "Lesson with citations " + " ".join(urls),
        [_source(url) for url in urls],
        _review(urls),
    )

    assert result["accepted"] is False
    assert "at_least_two_distinct_https_sources" in result["failed_gates"]


def test_evidence_gate_rejects_http_short_excerpt_and_missing_citation():
    urls = ["http://docs.example.org/a", "https://other.example.org/b"]
    sources = [_source(url, "short") for url in urls]
    result = evaluate_lesson_evidence("Lesson cites no valid source", sources, _review(urls))

    assert result["accepted"] is False
    assert result["source_count"] == 0
    assert "at_least_two_source_urls_cited_exactly" in result["failed_gates"]


def test_evidence_gate_rejects_conflicts_or_non_supported_claims():
    urls = ["https://docs.example.org/a", "https://other.example.org/b"]
    review = _review(urls)
    review["claims"][0]["verdict"] = "insufficient_evidence"
    review["source_conflicts"] = [{"topic": "example"}]
    result = evaluate_lesson_evidence(" ".join(urls), [_source(url) for url in urls], review)

    assert result["accepted"] is False
    assert "all_claims_supported_by_known_sources" in result["failed_gates"]
    assert "no_reported_source_conflicts" in result["failed_gates"]


def test_evidence_gate_does_not_trust_unknown_claim_evidence_urls():
    urls = ["https://docs.example.org/a", "https://other.example.org/b"]
    review = _review(["https://attacker.example/forged"])
    result = evaluate_lesson_evidence(" ".join(urls), [_source(url) for url in urls], review)

    assert result["accepted"] is False
    assert "all_claims_supported_by_known_sources" in result["failed_gates"]


def test_evidence_gate_does_not_accept_url_substrings_as_exact_citations():
    urls = ["https://docs.python.org/3/tutorial/index.html", "https://learn.microsoft.com/en-us/sql/"]
    lesson = "Citation with a forged suffix: " + urls[0] + "/unrelated and exact citation: " + urls[1]
    result = evaluate_lesson_evidence(lesson, [_source(url) for url in urls], _review(urls))

    assert result["accepted"] is False
    assert result["cited_source_count"] == 1
    assert "at_least_two_source_urls_cited_exactly" in result["failed_gates"]


def test_every_claim_must_reference_sources_actually_cited_in_lesson():
    urls = ["https://docs.python.org/3/tutorial/index.html", "https://learn.microsoft.com/en-us/sql/"]
    lesson = "Lesson cites only one source: " + urls[0]
    result = evaluate_lesson_evidence(lesson, [_source(url) for url in urls], _review(urls))

    assert result["accepted"] is False
    assert "all_claims_supported_by_known_sources" in result["failed_gates"]


def test_primary_source_allowlist_rejects_host_suffix_and_credential_spoofing():
    from app.learning_evidence_gate import is_allowlisted_primary_source

    assert is_allowlisted_primary_source("https://docs.python.org/3/")
    assert not is_allowlisted_primary_source("https://python.org.attacker.example/")
    assert not is_allowlisted_primary_source("https://python.org@attacker.example/")
    assert not is_allowlisted_primary_source("http://docs.python.org/3/")


def test_evidence_gate_fails_closed_on_unhashable_claim_evidence_url():
    urls = ["https://docs.python.org/3/tutorial/index.html", "https://learn.microsoft.com/en-us/sql/"]
    review = _review(urls)
    review["claims"][0]["evidence_urls"] = [{"url": urls[0]}]

    result = evaluate_lesson_evidence(
        "Lesson cites " + " ".join(urls), [_source(url) for url in urls], review
    )

    assert result["accepted"] is False
    assert "all_claims_supported_by_known_sources" in result["failed_gates"]



def test_evidence_gate_rejects_claim_without_explicit_text_or_reason():
    urls = ["https://docs.python.org/3/tutorial/index.html", "https://learn.microsoft.com/en-us/sql/"]
    review = _review(urls)
    review["claims"][0]["claim"] = "   "
    review["claims"][0]["reason"] = ""

    result = evaluate_lesson_evidence(
        "Lesson cites " + " ".join(urls), [_source(url) for url in urls], review
    )

    assert result["accepted"] is False
    assert result["explicitly_mapped_claim_count"] == 0
    assert "all_claims_supported_by_known_sources" in result["failed_gates"]


def test_versioned_golden_evidence_gate_cases():
    fixture_path = Path(__file__).parent / "fixtures" / "learning_evidence_golden_v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1
    cases = fixture["cases"]
    assert len(cases) >= 5
    assert len({case["id"] for case in cases}) == len(cases)

    for case in cases:
        result = evaluate_lesson_evidence(
            case["lesson"], case["sources"], case["claim_review"]
        )
        assert result["accepted"] is case["expected_accepted"], case["id"]
        if result["accepted"]:
            assert result["independent_scientific_verification"] is False
            assert result["status"] == "evidence_gate_passed_not_proof_of_truth"
        else:
            assert result["failed_gates"], case["id"]
