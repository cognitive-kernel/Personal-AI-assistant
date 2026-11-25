"""Run-scoped creation metadata and safe cleanup for temporary Ollama Codex variants."""

from __future__ import annotations

import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

_BASE_URL = "http://127.0.0.1:11434"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_TEMPORARY_NAME = re.compile(
    r"^(?P<base>.+)-codex-ctx(?P<context>[0-9]+)-paa-(?P<run_id>[0-9]+)-(?P<attempt>[0-9]+)-(?P<nonce>[a-f0-9]{12})$",
    re.IGNORECASE,
)
_MODFILE_NUM_CTX = re.compile(r"(?im)^\s*PARAMETER\s+num_ctx\s+([0-9]+)\s*$")
_PARAMETERS_NUM_CTX = re.compile(r"(?im)^\s*num_ctx\s+([0-9]+)\s*$")

RequestJson = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


class OllamaTemporaryContextError(RuntimeError):
    """Temporary Ollama model ownership or cleanup could not be verified safely."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def temporary_context_model_name(
    base_model: str,
    context: int,
    *,
    run_id: str,
    run_attempt: str,
    nonce: str | None = None,
) -> str:
    """Build a collision-resistant tag that can only be cleaned by its owning run."""
    if not isinstance(base_model, str) or not base_model.strip() or len(base_model) > 140:
        raise ValueError("base_model must be a non-empty model tag of at most 140 characters.")
    if any(character.isspace() for character in base_model):
        raise ValueError("base_model must not contain whitespace.")
    if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
        raise ValueError("context must be a positive integer.")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]{1,24}", run_id):
        raise ValueError("run_id must contain 1 to 24 decimal digits.")
    if not isinstance(run_attempt, str) or not re.fullmatch(r"[0-9]{1,6}", run_attempt):
        raise ValueError("run_attempt must contain 1 to 6 decimal digits.")
    nonce = nonce or secrets.token_hex(6)
    if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{12}", nonce):
        raise ValueError("nonce must be exactly 12 lowercase hexadecimal characters.")
    result = f"{base_model}-codex-ctx{context}-paa-{run_id}-{run_attempt}-{nonce}"
    if len(result) > 200:
        raise ValueError("Generated temporary model tag exceeds Ollama's supported name bound.")
    return result


def _request_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    if path not in {"/api/tags", "/api/ps", "/api/show", "/api/generate", "/api/delete"}:
        raise ValueError("Unsupported Ollama temporary-context endpoint.")
    if method not in {"GET", "POST", "DELETE"}:
        raise ValueError("Unsupported Ollama temporary-context HTTP method.")
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        _BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise OllamaTemporaryContextError(
            f"Ollama {method} {path} failed with HTTP status {exc.code}."
        ) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise OllamaTemporaryContextError(
            f"Ollama {method} {path} failed ({type(exc).__name__})."
        ) from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OllamaTemporaryContextError(f"Ollama {method} {path} response exceeded the size limit.")
    if not raw:
        return {}
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaTemporaryContextError(f"Ollama {method} {path} returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise OllamaTemporaryContextError(f"Ollama {method} {path} returned an invalid response object.")
    return result


def _model_names(payload: dict[str, Any], endpoint: str) -> list[str]:
    models = payload.get("models")
    if not isinstance(models, list):
        raise OllamaTemporaryContextError(f"Ollama {endpoint} did not return a model list.")
    result: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            raise OllamaTemporaryContextError(f"Ollama {endpoint} returned an invalid model entry.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise OllamaTemporaryContextError(f"Ollama {endpoint} returned a model without a name.")
        result.append(name.strip())
    return result


def _saved_context(info: dict[str, Any]) -> int | None:
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


def cleanup_temporary_context_model(
    model_name: str,
    *,
    run_id: str,
    run_attempt: str,
    expected_context: int,
    request_json: RequestJson | None = None,
    sleep: Callable[[float], None] = time.sleep,
    verify_attempts: int = 12,
    verify_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    """Unload and delete only the exact temporary variant owned by this workflow attempt.

    The original base-model tag is required to remain installed. Unknown/legacy
    variants, mismatched contexts, other workflow attempts, and still-loaded
    models are preserved.
    """
    if not isinstance(model_name, str) or len(model_name) > 200:
        raise ValueError("model_name must be a model tag of at most 200 characters.")
    if isinstance(expected_context, bool) or not isinstance(expected_context, int) or expected_context <= 0:
        raise ValueError("expected_context must be a positive integer.")
    if isinstance(verify_attempts, bool) or not isinstance(verify_attempts, int) or not 1 <= verify_attempts <= 30:
        raise ValueError("verify_attempts must be an integer between 1 and 30.")
    if not isinstance(verify_interval_seconds, (int, float)) or not 0 <= verify_interval_seconds <= 5:
        raise ValueError("verify_interval_seconds must be between 0 and 5 seconds.")

    match = _TEMPORARY_NAME.fullmatch(model_name)
    if (
        match is None
        or match.group("run_id") != str(run_id)
        or match.group("attempt") != str(run_attempt)
        or int(match.group("context")) != expected_context
    ):
        raise OllamaTemporaryContextError(
            "Refusing cleanup: model tag does not belong to this run attempt and context."
        )

    api = request_json or _request_json
    catalog = _model_names(api("GET", "/api/tags", None, 10.0), "/api/tags")
    folded_catalog = {name.casefold() for name in catalog}
    if model_name.casefold() not in folded_catalog:
        return {"model": model_name, "base_model": match.group("base"), "removed": False, "already_absent": True}

    base_model = match.group("base")
    if base_model.casefold() not in folded_catalog:
        raise OllamaTemporaryContextError(
            "Refusing cleanup: the original base-model tag is no longer installed; preserving the temporary variant."
        )

    info = api("POST", "/api/show", {"model": model_name, "verbose": True}, 20.0)
    saved_context = _saved_context(info)
    if saved_context != expected_context:
        raise OllamaTemporaryContextError(
            "Refusing cleanup: the temporary model's saved num_ctx does not match its run-scoped tag."
        )

    loaded = {
        name.casefold()
        for name in _model_names(api("GET", "/api/ps", None, 10.0), "/api/ps")
    }
    if model_name.casefold() in loaded:
        api(
            "POST",
            "/api/generate",
            {"model": model_name, "prompt": "", "stream": False, "keep_alive": 0},
            30.0,
        )
        unloaded = False
        for attempt in range(verify_attempts):
            current = {
                name.casefold()
                for name in _model_names(api("GET", "/api/ps", None, 10.0), "/api/ps")
            }
            if model_name.casefold() not in current:
                unloaded = True
                break
            if attempt + 1 < verify_attempts:
                sleep(verify_interval_seconds)
        if not unloaded:
            raise OllamaTemporaryContextError(
                "Refusing deletion: Ollama still reports the temporary model as loaded."
            )

    # Recheck both the base tag and loaded state immediately before deletion.
    latest_catalog = {
        name.casefold()
        for name in _model_names(api("GET", "/api/tags", None, 10.0), "/api/tags")
    }
    if base_model.casefold() not in latest_catalog:
        raise OllamaTemporaryContextError(
            "Refusing cleanup: the original base-model tag disappeared during cleanup."
        )
    if model_name.casefold() not in latest_catalog:
        return {"model": model_name, "base_model": base_model, "removed": False, "already_absent": True}
    latest_loaded = {
        name.casefold()
        for name in _model_names(api("GET", "/api/ps", None, 10.0), "/api/ps")
    }
    if model_name.casefold() in latest_loaded:
        raise OllamaTemporaryContextError(
            "Refusing deletion: the temporary model became loaded again during cleanup."
        )

    try:
        api("DELETE", "/api/delete", {"model": model_name}, 30.0)
    except OllamaTemporaryContextError as exc:
        if "HTTP status 404" not in str(exc):
            raise

    for attempt in range(verify_attempts):
        refreshed = {
            name.casefold()
            for name in _model_names(api("GET", "/api/tags", None, 10.0), "/api/tags")
        }
        if model_name.casefold() not in refreshed:
            return {"model": model_name, "base_model": base_model, "removed": True, "already_absent": False}
        if attempt + 1 < verify_attempts:
            sleep(verify_interval_seconds)

    raise OllamaTemporaryContextError(
        "Ollama acknowledged temporary-model deletion but still lists the model tag."
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Unload and remove one run-owned Ollama context variant.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--context", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = cleanup_temporary_context_model(
            args.model,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            expected_context=args.context,
        )
    except (OllamaTemporaryContextError, OSError, ValueError) as exc:
        print(f"Ollama temporary-context cleanup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
