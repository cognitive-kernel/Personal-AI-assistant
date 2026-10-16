from app.cloud_resource_planner import ResourcePlanningError, build_selection_plan


def _candidate(**overrides):
    candidate = {
        "id": 7,
        "provider_domain": "compute.example.org",
        "content_sha256": "a" * 64,
        "reviewed_content_sha256": "a" * 64,
        "review_status": "approved",
        "freshness_status": "fresh",
        "availability_status": "available",
        "terms_url": "https://compute.example.org/terms",
        "quota": {
            "max_cost_usd": 0,
            "auth_method": "anonymous",
            "selected_region": "us-east-1",
            "allowed_regions": ["us-east-1"],
            "max_session_minutes": 60,
            "max_monthly_hours": 100,
        },
        "observed_claims": [
            _claim("cpu_count", 4),
            _claim("memory_capacity", 16, "GiB"),
            _claim("usage_quota", "100 hours"),
            _claim("free_tier_mention", "free tier"),
            _claim("region", "us-east-1"),
        ],
    }
    candidate.update(overrides)
    return candidate


def _claim(claim_type, value, unit=None, **overrides):
    claim = {
        "source_content_sha256": "a" * 64,
        "claim_type": claim_type,
        "value": value,
        "unit": unit,
        "review_status": "accepted",
        "is_current": 1,
    }
    claim.update(overrides)
    return claim


def test_planner_selects_only_current_reviewed_policy_ready_candidate():
    candidate = _candidate()
    plan = build_selection_plan(
        [candidate],
        {"min_cpu_count": 2, "min_memory_gib": 8},
    )

    assert plan["status"] == "candidate_found"
    assert plan["selected_candidate_id"] == 7
    assert plan["execution_authorized"] is False
    assert plan["remote_execution_enabled"] is False
    assert plan["candidates"][0]["eligible_for_adapter_review"] is True


def test_planner_fails_closed_when_capacity_claims_are_missing_stale_or_unaccepted():
    candidate = _candidate(observed_claims=[
        _claim("cpu_count", 64, source_content_sha256="b" * 64),
        _claim("memory_capacity", 512, "GiB", review_status="unreviewed"),
        _claim("usage_quota", "100 hours"),
        _claim("free_tier_mention", "free tier"),
        _claim("region", "us-east-1"),
    ])
    plan = build_selection_plan([candidate], {"min_cpu_count": 1})

    assert plan["status"] == "no_eligible_candidate"
    assert plan["selected_candidate_id"] is None
    assert plan["candidates"][0]["eligible_for_adapter_review"] is False
    assert any("CPU capacity is unknown" in reason for reason in plan["candidates"][0]["reasons"])
    assert any("memory capacity" in reason for reason in plan["candidates"][0]["reasons"])


def test_planner_checks_gpu_models_memory_units_and_provider_allowlist():
    candidate = _candidate(observed_claims=[
        _claim("cpu_count", 8),
        _claim("memory_capacity", 32, "GB"),
        _claim("gpu_model", "NVIDIA A10"),
        _claim("usage_quota", "100 hours"),
        _claim("free_tier_mention", "free tier"),
        _claim("region", "us-east-1"),
    ])
    eligible = build_selection_plan(
        [candidate],
        {
            "min_cpu_count": 4,
            "min_memory_gib": 29,
            "require_gpu": True,
            "gpu_models": ["nvidia a10"],
            "allowed_provider_domains": ["COMPUTE.EXAMPLE.ORG"],
        },
    )
    blocked = build_selection_plan(
        [candidate],
        {
            "min_cpu_count": 4,
            "min_memory_gib": 29,
            "require_gpu": True,
            "gpu_models": ["NVIDIA H100"],
            "allowed_provider_domains": ["other.example.org"],
        },
    )

    assert eligible["selected_candidate_id"] == 7
    assert eligible["candidates"][0]["capabilities"]["memory_gib"] < 30
    assert blocked["status"] == "no_eligible_candidate"
    assert any("allowlist" in reason for reason in blocked["candidates"][0]["reasons"])
    assert any("GPU model" in reason for reason in blocked["candidates"][0]["reasons"])


def test_planner_validates_requirements_and_untrusted_candidate_shapes():
    for requirements in (
        {"min_cpu_count": True},
        {"min_memory_gib": float("nan")},
        {"require_gpu": "yes"},
        {"gpu_models": "NVIDIA"},
        {"allowed_provider_domains": [None]},
    ):
        try:
            build_selection_plan([], requirements)
        except ResourcePlanningError:
            pass
        else:
            raise AssertionError(f"Invalid requirements unexpectedly accepted: {requirements!r}")

    malformed = _candidate(observed_claims=[
        _claim("cpu_count", 10 ** 1000),
        _claim("memory_capacity", float("inf"), "GiB"),
    ])
    plan = build_selection_plan([malformed], {"min_cpu_count": 1})
    assert plan["status"] == "no_eligible_candidate"
    assert plan["execution_authorized"] is False
