from pathlib import Path


def test_master_prompt_defines_universal_version_aware_learning_contract():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "MASTER_ENGINEERING_PROMPT.md"
    ).read_text(encoding="utf-8")

    required_contract = (
        "Universal, Version-Aware Learning Contract (Mandatory for Every Subject)",
        "never constrain a syllabus to learner examples",
        "earliest documented public/initial/stable release",
        "strictly oldest-to-newest",
        "Publisher authority is a confidence attribute, never an admission rule",
        "Context-window and per-request token budgets may require resumable evidence batches",
        "A learner report that code or instructions failed is a valid trigger",
        "Default the learning lane to gpt-oss:20b",
        "Provider, model/LLM, endpoint, and role-specific routing must be changeable in application settings",
        "Do not stop development merely because target-host or human acceptance tests are unavailable",
    )
    for requirement in required_contract:
        assert requirement in prompt
