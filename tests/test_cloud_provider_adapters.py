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
    assert all(claim.evidence_quote in (title + "\n" + text) for claim in claims)
    assert all(claim.adapter_id == "generic-public-page-regex-v1" for claim in claims)


def test_generic_adapter_does_not_infer_missing_capacity_or_availability():
    text = "Compute may be available in some regions. Free plan terms vary and quotas can change."

    claims = GenericPublicPageAdapter().extract_claims("Compute", text)

    assert {claim.claim_type for claim in claims} == {"free_tier_mention"}


def test_generic_adapter_deduplicates_repeated_claims():
    text = "2 vCPU and 4 vCPU; 2 vCPU is the stated limit."

    claims = GenericPublicPageAdapter().extract_claims("CPU", text)

    assert sum(claim.claim_type == "cpu_count" for claim in claims) == 2



def test_kaggle_adapter_only_matches_exact_official_cli_documentation():
    from app.cloud_provider_adapters import KaggleCliDocsAdapter

    adapter = KaggleCliDocsAdapter()
    assert adapter.matches(
        "github.com",
        "https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md",
    )
    assert not adapter.matches(
        "github.com",
        "https://github.com/someone/kaggle-cli/blob/main/docs/kernels.md",
    )
    assert not adapter.matches(
        "github.com",
        "https://github.com/Kaggle/kaggle-cli/blob/main/README.md",
    )
    assert not adapter.matches(
        "github.com",
        "https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md?raw=1",
    )
    assert not adapter.matches(
        "kaggle.com",
        "https://kaggle.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md",
    )


def test_kaggle_adapter_extracts_only_documented_options_not_entitlements():
    source_url = "https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md"
    text = (
        "The --accelerator option accepts NvidiaTeslaT4 and NvidiaL4. "
        "Use kaggle kernels push to run the kernel, then kaggle kernels status "
        "to inspect it. Some accelerator types may be restricted to competitions."
    )
    claims = extract_resource_claims(
        "github.com",
        "Kaggle Kernels Commands",
        text,
        source_url=source_url,
    )

    assert {claim.claim_type for claim in claims} == {
        "provider_accelerator_option",
        "provider_operation",
    }
    assert {claim.value for claim in claims if claim.claim_type == "provider_accelerator_option"} == {
        "NvidiaTeslaT4",
        "NvidiaL4",
    }
    assert {claim.value for claim in claims if claim.claim_type == "provider_operation"} == {
        "kaggle kernels push",
        "kaggle kernels status",
    }
    assert all(claim.adapter_id == "kaggle-cli-docs-v1" for claim in claims)
    assert all(claim.evidence_quote in ("Kaggle Kernels Commands\n" + text) for claim in claims)
    assert all("unverified" in (claim.unit or "") or "not an execution authorization" in (claim.unit or "")
               for claim in claims)


def test_generic_adapter_remains_fallback_for_other_github_sources():
    claims = extract_resource_claims(
        "github.com",
        "Free compute",
        "The free tier includes 2 vCPU and 8 GB RAM.",
        source_url="https://github.com/other/project/blob/main/README.md",
    )

    assert {claim.claim_type for claim in claims} == {"cpu_count", "memory_capacity"}
    assert all(claim.adapter_id == "generic-public-page-regex-v1" for claim in claims)
