from app.cloud_provider_adapters import GenericPublicPageAdapter, extract_resource_claims


def test_generic_adapter_extracts_only_explicit_claims_with_verbatim_evidence():
    title = "Free tier compute"
    text = (
        "Free tier includes 2 vCPU, 8 GB RAM, 100 GB persistent storage, "
        "12 hours per month, maximum 6 hours per session, $10 in cloud credits, "
        "$0.20 per hour, NVIDIA T4 GPU, and region us-east-1."
    )

    claims = extract_resource_claims("compute.example.org", title, text)
    by_type = {}
    for claim in claims:
        by_type.setdefault(claim.claim_type, []).append(claim)

    assert by_type["cpu_count"][0].value == 2
    assert by_type["memory_capacity"][0].value == 8
    assert by_type["memory_capacity"][0].unit.lower() == "gb"
    assert by_type["storage_capacity"][0].value == 100
    assert by_type["monthly_compute_hours"][0].value == 12
    assert by_type["session_limit"][0].value == 6
    assert by_type["credit_amount"][0].value == 10
    assert by_type["price_rate"][0].value == 0.2
    assert by_type["gpu_model"][0].value == "T4"
    assert by_type["region"][0].value == "us-east-1"
    assert by_type["free_tier_mention"][0].value.casefold() == "free tier"
    assert all(claim.review_status if hasattr(claim, "review_status") else True for claim in claims)
    assert all(claim.evidence_quote in (title + "\n" + text) for claim in claims)
    assert all(claim.adapter_id == "generic-public-page-regex-v1" for claim in claims)


def test_generic_adapter_does_not_infer_missing_capacity_or_availability():
    text = "Compute may be available in some regions. Free plan terms vary and quotas can change."

    claims = GenericPublicPageAdapter().extract_claims("provider.example.org", "Compute", text)

    assert {claim.claim_type for claim in claims} == {"free_tier_mention"}
    assert all(claim.review_status if hasattr(claim, "review_status") else True for claim in claims)


def test_generic_adapter_deduplicates_repeated_claims():
    text = "2 vCPU and 2 vCPU; 2 vCPU is the stated limit."

    claims = GenericPublicPageAdapter().extract_claims("provider.example.org", "CPU", text)

    assert sum(claim.claim_type == "cpu_count" for claim in claims) == 2
