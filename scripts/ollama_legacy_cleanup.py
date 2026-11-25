"""Audit legacy numeric-suffix Codex aliases; destructive deletion requires explicit opt-in."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

_BASE_URL = "http://127.0.0.1:11434"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_LEGACY_NAME = re.compile(
    r"^(?P<base>.+)-codex-ctx(?P<context>[0-9]+)$",
    re.IGNORECASE,
)
_MODFILE_NUM_CTX = re.compile(
    r"(?im)^\s*PARAMETER\s+num_ctx\s+([0-9]+)\s*$"
)
_PARAMETERS_NUM_CTX = re.compile(
    r"(?im)^\s*num_ctx\s+([0-9]+)\s*$"
)

RequestJson = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


class OllamaCleanupError(RuntimeError):
    """The local Ollama inventory or cleanup result could not be verified safely."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Call only the loopback Ollama API; do not send local traffic through a proxy."""
    if path not in {"/api/tags", "/api/ps", "/api/show", "/api/delete"}:
        raise ValueError("Unsupported Ollama cleanup endpoint.")
    if method not in {"GET", "POST", "DELETE"}:
        raise ValueError("Unsupported Ollama cleanup HTTP method.")
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        _BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise OllamaCleanupError(
            f"Ollama {method} {path} failed with HTTP status {exc.code}."
        ) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise OllamaCleanupError(
            f"Ollama {method} {path} failed ({type(exc).__name__})."
        ) from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OllamaCleanupError(f"Ollama {method} {path} response exceeded the size limit.")
    if not raw:
        return {}
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaCleanupError(f"Ollama {method} {path} returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise OllamaCleanupError(f"Ollama {method} {path} returned an invalid response object.")
    return result


def _model_names(payload: dict[str, Any], endpoint: str) -> list[str]:
    models = payload.get("models")
    if not isinstance(models, list):
        raise OllamaCleanupError(f"Ollama {endpoint} did not return a model list.")
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            raise OllamaCleanupError(f"Ollama {endpoint} returned an invalid model entry.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise OllamaCleanupError(f"Ollama {endpoint} returned a model without a name.")
        names.append(name.strip())
    return names


def _saved_context(info: dict[str, Any]) -> int | None:
    """Return the persistent positive num_ctx declared by Ollama /api/show."""
    modelfile = info.get("modelfile")
    if isinstance(modelfile, str):
        match = _MODFILE_NUM_CTX.search(modelfile)
        if match:
            value = int(match.group(1))
            return value if value > 0 else None

    parameters = info.get("parameters")
    if isinstance(parameters, str):
        match = _PARAMETERS_NUM_CTX.search(parameters)
        if match:
            value = int(match.group(1))
            return value if value > 0 else None
    return None


def cleanup_legacy_context_variants(
    current_model: str,
    *,
    request_json: RequestJson | None = None,
    audit_only: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    verify_attempts: int = 5,
    verify_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Audit legacy numeric aliases across every base-model family, optionally deleting only when explicitly requested.

    A tag is eligible only when its name ends in -codex-ctxN and Ollama's saved
    Modelfile/parameters independently declares the same positive num_ctx.
    Base tags, stable -codex-ctx tags, -codex-64k tags, unverified aliases,
    the current model, and loaded aliases are kept.
    """
    if not isinstance(current_model, str) or not current_model.strip() or len(current_model) > 200:
        raise ValueError("A non-empty current model name of at most 200 characters is required.")
    if isinstance(verify_attempts, bool) or not isinstance(verify_attempts, int) or not 1 <= verify_attempts <= 20:
        raise ValueError("verify_attempts must be an integer between 1 and 20.")
    if not isinstance(verify_interval_seconds, (int, float)) or not 0 <= verify_interval_seconds <= 5:
        raise ValueError("verify_interval_seconds must be between 0 and 5 seconds.")

    api = request_json or _request_json
    current_lower = current_model.strip().casefold()
    catalog_names = _model_names(api("GET", "/api/tags", None, 10.0), "/api/tags")
    loaded_names = {
        name.casefold()
        for name in _model_names(api("GET", "/api/ps", None, 10.0), "/api/ps")
    }

    summary: dict[str, Any] = {
        "current_model": current_model.strip(),
        "audit_only": bool(audit_only),
        "scanned_model_count": len(catalog_names),
        "candidate_count": 0,
        "removed_models": [],
        "would_remove_models": [],
        "preserved_loaded": [],
        "preserved_current": [],
        "preserved_unverified": [],
    }

    candidates: list[tuple[str, int]] = []
    for name in catalog_names:
        match = _LEGACY_NAME.fullmatch(name)
        if not match:
            continue
        suffix_context = int(match.group("context"))
        if suffix_context <= 0:
            summary["preserved_unverified"].append(
                {"name": name, "reason": "non_positive_context_suffix"}
            )
            continue
        candidates.append((name, suffix_context))

    summary["candidate_count"] = len(candidates)

    for name, suffix_context in candidates:
        folded_name = name.casefold()
        if folded_name == current_lower:
            summary["preserved_current"].append(name)
            continue
        if folded_name in loaded_names:
            summary["preserved_loaded"].append(name)
            continue

        info = api("POST", "/api/show", {"model": name, "verbose": True}, 20.0)
        saved_context = _saved_context(info)
        if saved_context is None:
            summary["preserved_unverified"].append(
                {"name": name, "reason": "persistent_num_ctx_not_found"}
            )
            continue
        if saved_context != suffix_context:
            summary["preserved_unverified"].append(
                {
                    "name": name,
                    "reason": "tag_context_does_not_match_saved_num_ctx",
                    "tag_context": suffix_context,
                    "saved_num_ctx": saved_context,
                }
            )
            continue

        if audit_only:
            summary["would_remove_models"].append(name)
            continue

        # Re-check immediately before deletion to narrow the race where a user
        # loads a legacy alias after the initial inventory snapshot.
        latest_loaded_names = {
            value.casefold()
            for value in _model_names(
                api("GET", "/api/ps", None, 10.0),
                "/api/ps",
            )
        }
        if folded_name in latest_loaded_names:
            summary["preserved_loaded"].append(name)
            continue

        try:
            api("DELETE", "/api/delete", {"model": name}, 30.0)
        except OllamaCleanupError as exc:
            if "HTTP status 404" not in str(exc):
                raise

        absent = False
        for attempt in range(verify_attempts):
            refreshed_names = _model_names(
                api("GET", "/api/tags", None, 10.0),
                "/api/tags",
            )
            if folded_name not in {value.casefold() for value in refreshed_names}:
                absent = True
                break
            if attempt + 1 < verify_attempts:
                sleep(verify_interval_seconds)

        if not absent:
            raise OllamaCleanupError(
                f"Ollama acknowledged deletion but still lists legacy tag '{name}'."
            )
        summary["removed_models"].append(name)

    summary["removed_count"] = len(summary["removed_models"])
    summary["would_remove_count"] = len(summary["would_remove_models"])
    summary["preserved_loaded_count"] = len(summary["preserved_loaded"])
    summary["preserved_unverified_count"] = len(summary["preserved_unverified"])
    summary["preserved_current_count"] = len(summary["preserved_current"])
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit legacy numeric Codex context aliases; deletion requires explicit confirmation."
    )
    parser.add_argument("--current-model", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--audit-only",
        action="store_true",
        help="Report eligible aliases without deleting anything (the default).",
    )
    mode.add_argument(
        "--delete-confirmed-legacy",
        action="store_true",
        help="Explicitly delete verified legacy aliases; never used by application startup.",
    )
    args = parser.parse_args(argv)
    try:
        result = cleanup_legacy_context_variants(
            args.current_model,
            audit_only=not args.delete_confirmed_legacy,
        )
    except (OllamaCleanupError, OSError, ValueError) as exc:
        print(f"Ollama legacy context cleanup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
