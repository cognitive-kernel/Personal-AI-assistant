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
        "Default the learning lane to `gpt-oss:20b`",
        "Provider, model/LLM, endpoint, and role-specific routing must be changeable in application settings",
        "Do not stop development merely because target-host or human acceptance tests are unavailable",
        "The user's subject name is the scope. Examples, questions, code snippets, current task, skill level, or supplied source text are context only",
        "Do not use fixed source counts, fixed numbers of versions, fixed years, fixed lesson totals, or fixed evidence totals as course-completion criteria",
        "Every retained source must eventually be considered by synthesis",
        "A course-level version label is insufficient",
        "Sort release groups by actual release chronology, not lexical version-string order",
        "A test that requires an unavailable owner machine, physical device, private environment, or human judgment is not silently treated as passed",
        "Owner-local, target-host, physical-device, and human acceptance activities are not merge gates by default",
        "Temporary overrides must be scoped to the requested operation/session",
        "A correction creates a new revision, links its predecessor, records the reason and evidence",
    )
    for requirement in required_contract:
        assert requirement in prompt

    assert "never merge to `main` before exact-head CI and required target-host tests actually pass" not in prompt
    assert "Owner-local, target-host, physical-device, and human acceptance activities are not merge gates by default" in prompt
