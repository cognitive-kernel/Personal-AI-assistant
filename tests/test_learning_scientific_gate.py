from app.learning_scientific_gate import evaluate_scientific_gate


def _sources():
    return [
        {
            "title": "Documentation A",
            "url": "https://docs-a.example/guide",
            "content": "A sufficiently long source excerpt describing a technical fact and its context.",
        },
        {
            "title": "Documentation B",
            "url": "https://docs-b.example/reference",
            "content": "Another sufficiently long excerpt from a separate source host supporting the lesson.",
        },
    ]


def _review():
    return {
        "passed": True,
        "reviewer": "local_ollama_model_assisted_not_independent",
        "claims": [{
            "claim": "A material claim",
            "verdict": "supported",
            "evidence_urls": ["https://docs-a.example/guide"],
            "reason": "Directly supported by the excerpt.",
        }],
        "source_conflicts": [],
    }


def test_gate_passes_only_as_machine_reviewed_not_independently_verified():
    lesson = "Lesson text. Sources: https://docs-a.example/guide"
    result = evaluate_scientific_gate(
        source_kind="web_retrieved", sources=_sources(),
        claim_review=_review(), lesson=lesson,
    )
    assert result["passed"] is True
    assert result["status"] == "machine_reviewed_not_independently_verified"
    assert result["independently_verified"] is False
    assert result["source_count"] == 2
    assert result["distinct_source_host_count"] == 2


def test_gate_fails_closed_without_retrieved_sources():
    result = evaluate_scientific_gate(
        source_kind="model_knowledge_no_external_source",
        sources=[], claim_review=None, lesson="A plausible lesson.",
    )
    assert result["passed"] is False
    assert result["status"] == "blocked"
    assert "external_retrieved_sources_required" in result["reasons"]
    assert "claim_review_missing" in result["reasons"]


def test_gate_rejects_two_urls_from_same_host():
    sources = _sources()
    sources[1]["url"] = "https://docs-a.example/other"
    result = evaluate_scientific_gate(
        source_kind="web_retrieved", sources=sources,
        claim_review=_review(), lesson="Sources: https://docs-a.example/guide",
    )
    assert result["passed"] is False
    assert "sources_must_come_from_two_distinct_hosts" in result["reasons"]


def test_gate_rejects_conflicts_and_untrusted_claim_urls():
    review = _review()
    review["source_conflicts"] = [{
        "topic": "conflicting fact",
        "source_a_url": "https://docs-a.example/guide",
        "source_b_url": "https://docs-b.example/reference",
        "description": "Sources disagree.",
    }]
    result = evaluate_scientific_gate(
        source_kind="web_retrieved", sources=_sources(),
        claim_review=review, lesson="Sources: https://docs-a.example/guide",
    )
    assert result["passed"] is False
    assert "unresolved_source_conflicts" in result["reasons"]


def test_gate_rejects_malformed_and_credentialed_source_urls():
    sources = _sources()
    sources[0]["url"] = "https://trusted.example@attacker.example/guide"
    result = evaluate_scientific_gate(
        source_kind="web_retrieved", sources=sources,
        claim_review=_review(), lesson="Sources: https://docs-b.example/reference",
    )
    assert result["passed"] is False
    assert result["source_count"] == 1
