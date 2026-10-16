from app.cloud_resource_policy import assess_candidate


def _candidate(**overrides):
    candidate = {
        "content_sha256": "a" * 64,
        "reviewed_content_sha256": "a" * 64,
        "review_status": "approved",
        "freshness_status": "fresh",
        "availability_status": "available",
        "terms_url": "https://provider.example.org/terms",
        "quota": {"max_cost_usd": 0},
    }
    candidate.update(overrides)
    return candidate


def _claim(claim_type, **overrides):
    claim = {
        "source_content_sha256": "a" * 64,
        "claim_type": claim_type,
        "value": 2,
        "unit": None,
        "review_status": "accepted",
        "is_current": 1,
    }
    claim.update(overrides)
    return claim


def test_preflight_is_ready_only_for_current_reviewed_zero_cost_evidence():
    assessment = assess_candidate(
        _candidate(),
        [
            _claim("cpu_count"),
            _claim("usage_quota"),
            _claim("free_tier_mention", value="free tier"),
        ],
    )

    assert assessment["status"] == "ready_for_provider_adapter"
    assert assessment["execution_authorized"] is False
    assert assessment["blockers"] == []
    assert "provider-specific adapter" in assessment["review_needed"][0]


def test_unreviewed_or_stale_claims_do_not_satisfy_preflight():
    assessment = assess_candidate(
        _candidate(),
        [
            _claim("cpu_count", review_status="unreviewed"),
            _claim("usage_quota", source_content_sha256="b" * 64),
            _claim("free_tier_mention", is_current=0),
        ],
    )

    assert assessment["status"] == "needs_review"
    assert assessment["execution_authorized"] is False
    assert len(assessment["review_needed"]) >= 3


def test_source_or_availability_blockers_prevent_readiness():
    assessment = assess_candidate(
        _candidate(review_status="unreviewed", availability_status="unknown"),
        [
            _claim("cpu_count"),
            _claim("usage_quota"),
            _claim("free_tier_mention", value="free tier"),
        ],
    )

    assert assessment["status"] == "blocked"
    assert assessment["execution_authorized"] is False
    assert any("approved" in reason for reason in assessment["blockers"])
    assert any("availability" in reason for reason in assessment["blockers"])


def test_positive_price_claim_or_nonzero_spend_limit_blocks_free_only_policy():
    claims = [
        _claim("cpu_count"),
        _claim("usage_quota"),
        _claim("free_tier_mention", value="free tier"),
        _claim("price_rate", value=0.01, unit="USD per hour"),
    ]
    positive_rate = assess_candidate(_candidate(), claims)
    nonzero_budget = assess_candidate(
        _candidate(quota={"max_cost_usd": 1}),
        claims[:-1],
    )

    assert positive_rate["status"] == "blocked"
    assert any("positive price-rate" in reason for reason in positive_rate["blockers"])
    assert nonzero_budget["status"] == "blocked"
    assert any("zero spend" in reason for reason in nonzero_budget["blockers"])


def test_policy_rejects_boolean_cost_as_numeric_zero():
    assessment = assess_candidate(_candidate(quota={"max_cost_usd": False}), [])

    assert assessment["status"] == "needs_review"
    assert any("numeric max_cost_usd" in reason for reason in assessment["review_needed"])
