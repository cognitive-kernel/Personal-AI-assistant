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
    assert "every_used_source_cited_exactly" in result["failed_gates"]


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
