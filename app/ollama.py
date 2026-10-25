from __future__ import annotations
import ipaddress
import json
import math
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from .db import get_settings
from .hardware import detect_hardware, ensure_resource_headroom, model_keep_alive, runtime_limits

class OllamaError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)


def _ensure_resource_headroom() -> None:
    try:
        ensure_resource_headroom(get_settings())
    except RuntimeError as exc:
        raise OllamaError(str(exc), retryable=True) from exc


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# Keep this process from running multiple model inferences at once across task lanes.
_INFERENCE_LOCK = threading.Lock()
_INFERENCE_PATHS = frozenset({"/api/chat", "/api/embed", "/api/generate"})

def _base_url() -> str:
    base = str(get_settings()["ollama_base_url"]).rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OllamaError("Ollama URL must be a valid HTTP(S) URL.")
    if parsed.username or parsed.password:
        raise OllamaError("Credentials in the Ollama URL are not supported.")
    if parsed.path or parsed.query or parsed.fragment:
        raise OllamaError("Ollama URL must be a base URL without a path, query, or fragment.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise OllamaError("Ollama URL contains an invalid port.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise OllamaError("Ollama URL contains an invalid port.")

    hostname = parsed.hostname
    # Never rely on name resolution for "localhost": normalize it to a loopback
    # literal so a modified hosts file cannot redirect local prompts off-machine.
    if hostname.lower() == "localhost":
        netloc = "127.0.0.1" if port is None else f"127.0.0.1:{port}"
        parsed = parsed._replace(netloc=netloc)
        base = urllib.parse.urlunparse(parsed)
        hostname = "127.0.0.1"
    try:
        address = ipaddress.ip_address(hostname)
        loopback = address.is_loopback
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            loopback = False
    except ValueError:
        loopback = False
    if not loopback:
        raise OllamaError("Only a loopback IP literal or exact localhost endpoint is allowed.")
    return base

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent a loopback Ollama endpoint from redirecting requests elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    settings = get_settings()
    if path in _INFERENCE_PATHS and settings.get("gpu_enabled", False) is not True:
        payload = dict(payload or {})
        options = payload.get("options")
        options = dict(options) if isinstance(options, dict) else {}
        options["num_gpu"] = 0
        payload["options"] = options
    request = urllib.request.Request(
        _base_url() + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    def perform_request() -> dict[str, Any]:
        # Never let environment-configured proxies forward local prompts elsewhere.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        with opener.open(
            request, timeout=timeout or int(settings["ollama_timeout_seconds"])
        ) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise OllamaError("Ollama response exceeded the 8 MiB limit.")
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict):
            raise OllamaError("Unexpected Ollama response shape.")
        return result

    try:
        if path in _INFERENCE_PATHS:
            # Bound both queue wait and inference time. Recheck resources only after
            # acquiring the lock so waiting requests cannot all pass a stale preflight.
            wait_timeout = timeout if timeout is not None else int(settings["ollama_timeout_seconds"])
            acquired = _INFERENCE_LOCK.acquire(timeout=max(1.0, float(wait_timeout)))
            if not acquired:
                raise OllamaError("Timed out waiting for the local inference budget.", retryable=True)
            try:
                _ensure_resource_headroom()
                return perform_request()
            finally:
                _INFERENCE_LOCK.release()
        return perform_request()
    except urllib.error.HTTPError as exc:
        # Redirects are deliberately not followed by _NoRedirectHandler. Keep their
        # message generic and permanent; never expose or fetch the redirect target.
        # Configuration errors are permanent; transient throttling/server errors retry.
        retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
        if 300 <= exc.code < 400:
            message = f"Ollama request failed: HTTP {exc.code}."
        else:
            message = f"Ollama returned HTTP {exc.code}."
        raise OllamaError(
            message,
            retryable=exc.code in retryable_statuses,
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OllamaError(f"Ollama request failed: {type(exc).__name__}", retryable=True) from exc

def health() -> dict[str, Any]:
    try:
        payload = _request("/api/tags", timeout=4)
        models = payload.get("models", [])
        return {"available": True, "model_count": len(models) if isinstance(models, list) else 0, "error": None}
    except OllamaError as exc:
        return {"available": False, "model_count": 0, "error": str(exc)}

def list_models() -> list[dict[str, Any]]:
    models = _request("/api/tags", timeout=5).get("models", [])
    if not isinstance(models, list):
        return []
    return [
        {
            "name": item.get("name"),
            "size": item.get("size"),
            "modified_at": item.get("modified_at"),
            "digest": item.get("digest") if isinstance(item.get("digest"), str) else None,
        }
        for item in models if isinstance(item, dict)
    ]

def chat(
    message: str,
    history: list[dict[str, str]] | None = None,
    system_prompt: str | None = None,
    *,
    purpose: str = "chat",
    model_override: str | None = None,
) -> str:
    if purpose not in {"chat", "learning", "programming", "factual_review", "research_plan"}:
        raise ValueError("purpose must be chat, learning, programming, factual_review, or research_plan.")
    settings = get_settings()
    prefix = purpose
    hardware = detect_hardware()
    limits = runtime_limits({
        "ollama_num_ctx": settings.get(f"{prefix}_ollama_num_ctx", settings.get("ollama_num_ctx", 4096)),
        "ollama_num_thread": settings.get(f"{prefix}_ollama_num_thread", settings.get("ollama_num_thread", 0)),
        "cpu_resource_limit_percent": settings.get("cpu_resource_limit_percent", 90),
        "ram_budget_max_gb": settings.get("ram_budget_max_gb", 25),
        "ram_safety_reserve_gb": settings.get("ram_safety_reserve_gb", 4),
    }, hardware)
    keep_alive = model_keep_alive(purpose, hardware, settings)
    request_timeout = int(settings.get(f"{prefix}_ollama_timeout_seconds", settings.get("ollama_timeout_seconds", 120)))
    if purpose in {"factual_review", "research_plan"}:
        request_timeout = max(1, min(request_timeout, 45))
    model = str(model_override if model_override is not None else settings.get(f"{prefix}_ollama_model", settings.get("ollama_model", "qwen2.5:7b"))).strip()
    if not model or len(model) > 200:
        raise ValueError("model must be a non-empty name of at most 200 characters.")
    messages = []
    if isinstance(system_prompt, str) and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()[:6000]})
    for item in history or []:
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
            messages.append({"role": item["role"], "content": item["content"][:12000]})
    message_limit = 16000 if purpose == "factual_review" else 12000
    if purpose == "programming":
        message_limit = min(72000, max(12000, int(settings.get("programming_source_max_chars", 12000)) + 6500))
    messages.append({"role": "user", "content": message[:message_limit]})
    options = {"num_ctx": limits["num_ctx"], "num_thread": limits["cpu_threads"]}
    if purpose == "factual_review":
        options["num_ctx"] = min(int(options["num_ctx"]), 4096)
        options["num_predict"] = 500
    elif purpose == "research_plan":
        options["num_ctx"] = min(int(options["num_ctx"]), 4096)
        options["num_predict"] = 700
    response = _request("/api/chat", {
        "model": model, "stream": False, "messages": messages, "keep_alive": keep_alive,
        "options": options,
    }, timeout=request_timeout)
    result = response.get("message", {}).get("content")
    if not isinstance(result, str) or not result.strip():
        raise OllamaError("Ollama returned an empty or unexpected chat response.", retryable=True)
    return result



def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Generate bounded local embeddings; reject malformed vectors rather than caching them."""
    if not isinstance(texts, list) or not texts or len(texts) > 32:
        raise OllamaError("Embedding input must contain between 1 and 32 texts.")
    normalized: list[str] = []
    for text in texts:
        if not isinstance(text, str) or not text.strip() or len(text) > 6000:
            raise OllamaError("Each embedding input must contain 1–6000 characters.")
        normalized.append(text.strip())

    settings = get_settings()
    selected_model = str(model or settings.get("ollama_embedding_model", "nomic-embed-text")).strip()
    if not selected_model or len(selected_model) > 200:
        raise OllamaError("Embedding model name is invalid.")

    configured_timeout = int(settings["ollama_timeout_seconds"])
    response = _request(
        "/api/embed",
        {"model": selected_model, "input": normalized},
        timeout=max(1, min(configured_timeout, 8)),
    )
    embeddings = response.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(normalized):
        raise OllamaError("Ollama returned an unexpected embedding count.")

    validated: list[list[float]] = []
    dimension: int | None = None
    for vector in embeddings:
        if not isinstance(vector, list) or not vector or len(vector) > 8192:
            raise OllamaError("Ollama returned an invalid embedding vector.")
        values: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise OllamaError("Ollama returned a non-finite or non-numeric embedding value.")
            values.append(float(value))
        if dimension is None:
            dimension = len(values)
        elif len(values) != dimension:
            raise OllamaError("Ollama returned embeddings with inconsistent dimensions.")
        validated.append(values)
    return validated
