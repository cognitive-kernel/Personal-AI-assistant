"""Central registry for versioned prompts and deterministic learning evaluators.

A version label alone is not enough for reproducibility: every invocation records
both the registered semantic version and the SHA-256 of its exact prompt payload.
This registry deliberately does not treat a prompt version as evidence of quality.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

PROMPT_REGISTRY_VERSION = 1

# Entries are intentionally explicit. Bumping prompt text requires a new version
# identifier and a matching registry entry, rather than silently reusing a label.
_PROMPTS: dict[str, dict[str, Any]] = {
    "lesson_generation": {
        "version": "learning-lesson-v1",
        "kind": "generation",
        "purpose": "Generate a temporary lesson candidate",
    },
    "lesson_repair": {
        "version": "learning-lesson-repair-v1",
        "kind": "repair",
        "purpose": "Perform the single bounded lesson repair attempt",
    },
    "peer_review": {
        "version": "learning-peer-review-v2",
        "kind": "review",
        "purpose": "Request independent peer critique without trust promotion",
    },
    "oracle_evaluation": {
        "version": "learning-oracle-evaluation-v1",
        "kind": "deterministic_evaluator",
        "purpose": "Evaluate Oracle decisions against an explicitly labeled dataset",
    },
}


class PromptRegistryError(ValueError):
    """A prompt invocation does not match a registered version."""


def prompt_version(role: str) -> str:
    entry = _PROMPTS.get(role)
    if entry is None:
        raise PromptRegistryError(f"Unknown prompt role: {role}")
    return str(entry["version"])


def fingerprint_prompt(role: str, version: str, payload: Any) -> dict[str, Any]:
    """Validate a role/version pair and hash the exact canonical invocation payload."""
    entry = _PROMPTS.get(role)
    if entry is None:
        raise PromptRegistryError(f"Unknown prompt role: {role}")
    if not isinstance(version, str) or version != entry["version"]:
        raise PromptRegistryError(f"Unregistered version for prompt role: {role}")
    try:
        encoded = json.dumps(
            {"role": role, "version": version, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PromptRegistryError("Prompt payload must be canonical JSON data.") from exc
    return {
        "role": role,
        "version": version,
        "registry_version": PROMPT_REGISTRY_VERSION,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def list_registered_prompts() -> list[dict[str, Any]]:
    """Return a detached, deterministic public snapshot of registered prompt roles."""
    return [
        {
            "role": role,
            "version": str(entry["version"]),
            "kind": str(entry["kind"]),
            "purpose": str(entry["purpose"]),
        }
        for role, entry in sorted(_PROMPTS.items())
    ]
