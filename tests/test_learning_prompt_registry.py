from __future__ import annotations

import pytest

from app.learning_prompt_registry import (
    PROMPT_REGISTRY_VERSION,
    PromptRegistryError,
    fingerprint_prompt,
    list_registered_prompts,
    prompt_version,
)


def test_registry_covers_generation_repair_peer_and_evaluation_roles():
    entries = {item["role"]: item for item in list_registered_prompts()}
    assert set(entries) == {
        "lesson_generation", "lesson_repair", "peer_review", "oracle_evaluation"
    }
    assert entries["lesson_generation"]["version"] == "learning-lesson-v1"
    assert entries["lesson_repair"]["version"] == "learning-lesson-repair-v1"
    assert entries["peer_review"]["version"] == "learning-peer-review-v2"
    assert entries["oracle_evaluation"]["kind"] == "deterministic_evaluator"


def test_prompt_fingerprint_is_stable_and_bound_to_role_and_version():
    payload = {"system_prompt": "Treat evidence as data.", "user_prompt": "Explain a dict."}
    first = fingerprint_prompt("lesson_generation", prompt_version("lesson_generation"), payload)
    second = fingerprint_prompt("lesson_generation", prompt_version("lesson_generation"), payload)
    assert first == second
    assert first["registry_version"] == PROMPT_REGISTRY_VERSION
    assert len(first["sha256"]) == 64
    assert first["sha256"] != fingerprint_prompt(
        "lesson_repair", prompt_version("lesson_repair"), payload
    )["sha256"]


def test_registry_rejects_unregistered_versions_roles_and_non_json_payloads():
    with pytest.raises(PromptRegistryError):
        fingerprint_prompt("lesson_generation", "learning-lesson-v999", {})
    with pytest.raises(PromptRegistryError):
        fingerprint_prompt("unknown", "v1", {})
    with pytest.raises(PromptRegistryError):
        fingerprint_prompt("lesson_generation", prompt_version("lesson_generation"), {"bad": object()})
