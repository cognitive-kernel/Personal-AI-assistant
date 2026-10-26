"""Bounded reviewer adapters for local Ollama models and explicitly approved external APIs."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from . import ollama

_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
_ENV_KEY = re.compile(r"^LEARNING_REVIEW_API_KEY_[A-Z0-9_]{2,80}$")


class ReviewProviderUnavailable(RuntimeError):
    """A reviewer cannot safely produce an attributable result."""


def _external_configs(settings: dict[str, Any]) -> list[dict[str, Any]]:
    raw = settings.get("learning_external_review_providers", [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(raw, list):
        return []
    providers = []
    for item in raw[:5]:
        if not isinstance(item, dict) or item.get("enabled") is not True or item.get("data_sharing_approved") is not True:
            continue
        provider_id = item.get("provider_id")
        model = item.get("model")
        revision = item.get("model_revision")
        api_key_env = item.get("api_key_env")
        base_url = item.get("base_url")
        allowlist = item.get("host_allowlist")
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            continue
        if provider_id == "ollama" or len(provider_id) > 30:
            continue
        if not isinstance(model, str) or not 1 <= len(model.strip()) <= 100:
            continue
        if not isinstance(revision, str) or not 1 <= len(revision.strip()) <= 120:
            continue
        if not isinstance(api_key_env, str) or not _ENV_KEY.fullmatch(api_key_env):
            continue
        if not isinstance(base_url, str) or len(base_url) > 500:
            continue
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.query or parsed.fragment
            or not parsed.path.rstrip("/").endswith("/v1")
        ):
            continue
        if not isinstance(allowlist, list) or host not in {
            value.lower().rstrip(".") for value in allowlist if isinstance(value, str)
        }:
            continue
        config_digest = hashlib.sha256(
            f"{provider_id}|{model.strip()}|{revision.strip()}|{base_url.rstrip('/')}".encode()
        ).hexdigest()
        providers.append({
            "kind": "openai_compatible",
            "provider_id": provider_id,
            "model": model.strip(),
            "model_revision": revision.strip(),
            "base_url": base_url.rstrip("/"),
            "host": host,
            "api_key_env": api_key_env,
            "config_digest": config_digest,
        })
    return providers


def _assert_public_provider_host(provider: dict[str, Any]) -> None:
    host = provider["host"]
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ReviewProviderUnavailable("External reviewer host could not be resolved.") from exc
    if not addresses:
        raise ReviewProviderUnavailable("External reviewer host has no address.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError as exc:
            raise ReviewProviderUnavailable("External reviewer returned an invalid IP address.") from exc
        if not ip.is_global:
            raise ReviewProviderUnavailable("External reviewer host resolved to a non-public IP; request blocked.")


def list_reviewers(settings: dict[str, Any], installed_models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose at most two reviewers, preferring provider diversity when configured."""
    primary_names = {
        str(settings.get(key, "")).strip()
        for key in ("ollama_model", "chat_ollama_model", "learning_ollama_model", "programming_ollama_model")
        if str(settings.get(key, "")).strip()
    }
    primary_digests = {
        str(item.get("digest")) for item in installed_models
        if isinstance(item, dict) and str(item.get("name", "")).strip() in primary_names and item.get("digest")
    }
    local = []
    seen_digests = set(primary_digests)
    for item in installed_models:
        if not isinstance(item, dict):
            continue
        name, digest = str(item.get("name", "")).strip(), str(item.get("digest", "")).strip()
        if not name or not digest or name in primary_names or digest in seen_digests or len(name) + len(digest) + 8 > 200:
            continue
        local.append({
            "kind": "ollama", "provider_id": "ollama", "model": name,
            "digest": digest, "model_id": f"{name}@{digest}",
            "reviewer_id": f"ollama:{name}@{digest}",
        })
        seen_digests.add(digest)
    external = []
    for provider in _external_configs(settings):
        external.append({
            **provider,
            "digest": provider["config_digest"],
            "model_id": f"{provider['provider_id']}:{provider['model']}@{provider['config_digest']}",
            "reviewer_id": f"{provider['provider_id']}:{provider['model']}@{provider['config_digest']}",
        })
    selected: list[dict[str, Any]] = []
    # External reviewers are opt-in and explicitly consented; pair with another provider when possible.
    if external:
        selected.append(external[0])
        distinct_external = next((item for item in external[1:] if item["provider_id"] != selected[0]["provider_id"]), None)
        if distinct_external is not None:
            selected.append(distinct_external)
        elif local:
            selected.append(local[0])
    if len(selected) < 2:
        for item in local:
            if all(
                item["digest"] != chosen["digest"]
                and (item["provider_id"] != chosen["provider_id"] or item["digest"] != chosen["digest"])
                for chosen in selected
            ):
                selected.append(item)
            if len(selected) == 2:
                break
    return selected[:2]


def run_reviewer(reviewer: dict[str, Any], prompt: str, *, system_prompt: str) -> dict[str, Any]:
    """Run one reviewer and return attributable output; no silent fallback or redirects."""
    if reviewer.get("kind") == "ollama":
        raw = ollama.chat(
            prompt,
            system_prompt=system_prompt,
            purpose="factual_review",
            model_override=str(reviewer["model"]),
        )
        if not isinstance(raw, str):
            raise ReviewProviderUnavailable("Local reviewer returned a non-text response.")
        return {
            "content": raw,
            "model": reviewer["model"],
            "provider_id": "ollama",
            "digest": reviewer["digest"],
            "model_id": reviewer["model_id"],
            "reviewer_id": reviewer["reviewer_id"],
        }
    if reviewer.get("kind") != "openai_compatible":
        raise ReviewProviderUnavailable("Unsupported reviewer adapter.")
    _assert_public_provider_host(reviewer)
    api_key = os.getenv(str(reviewer["api_key_env"]), "").strip()
    if not api_key or len(api_key) > 4096:
        raise ReviewProviderUnavailable("External reviewer API key is missing or invalid.")
    payload = {
        "model": reviewer["model"],
        "temperature": 0,
        "max_tokens": 500,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(45.0, connect=8.0), follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST",
                reviewer["base_url"] + "/chat/completions",
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.is_redirect:
                    raise ReviewProviderUnavailable("External reviewer redirects are blocked.")
                response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 65536:
                        raise ReviewProviderUnavailable("External reviewer response exceeded 64 KiB.")
                body = json.loads(chunks.decode("utf-8"))
    except ReviewProviderUnavailable:
        raise
    except Exception as exc:
        raise ReviewProviderUnavailable(f"External reviewer request failed ({type(exc).__name__}).") from exc
    if not isinstance(body, dict) or not isinstance(body.get("system_fingerprint"), str) or not body["system_fingerprint"].strip():
        raise ReviewProviderUnavailable("External reviewer supplied no system_fingerprint; its output cannot count as an attributable independent review.")
    actual_model = body.get("model")
    choices = body.get("choices")
    if not isinstance(actual_model, str) or not 1 <= len(actual_model.strip()) <= 100 or not isinstance(choices, list) or not choices:
        raise ReviewProviderUnavailable("External reviewer response schema is invalid.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or len(content) > 12000:
        raise ReviewProviderUnavailable("External reviewer content is missing or exceeds its limit.")
    digest = hashlib.sha256(
        f"{reviewer['provider_id']}|{actual_model}|{reviewer['model_revision']}|{body['system_fingerprint']}".encode()
    ).hexdigest()
    return {
        "content": content,
        "model": actual_model,
        "provider_id": reviewer["provider_id"],
        "digest": digest,
        "model_id": f"{reviewer['provider_id']}:{actual_model}@{digest}",
        "reviewer_id": f"{reviewer['provider_id']}:{actual_model}@{digest}",
        "system_fingerprint": body["system_fingerprint"],
    }
