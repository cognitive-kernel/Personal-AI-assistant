from __future__ import annotations
import ipaddress
import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from .db import get_settings
from .hardware import detect_hardware, runtime_limits

class OllamaError(RuntimeError):
    pass


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024

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
    request = urllib.request.Request(
        _base_url() + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
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
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc

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

def chat_completion(
    model: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    response_format: str | dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run one chat completion for an explicitly selected model profile."""
    selected_model = model.strip() if isinstance(model, str) else ""
    if not selected_model or len(selected_model) > 200:
        raise OllamaError("Selected model name is invalid.")
    if not isinstance(messages, list) or not messages or len(messages) > 64:
        raise OllamaError("Chat messages must contain between 1 and 64 entries.")
    settings = get_settings()
    limits = runtime_limits(settings, detect_hardware())
    effective_options: dict[str, Any] = {
        "num_ctx": limits["num_ctx"],
        "num_thread": limits["cpu_threads"],
    }
    if isinstance(options, dict):
        effective_options.update(options)
    payload: dict[str, Any] = {
        "model": selected_model,
        "stream": False,
        "messages": messages,
        "options": effective_options,
    }
    if response_format is not None:
        payload["format"] = response_format
    return _request("/api/chat", payload, timeout=timeout)


def chat(
    message: str,
    history: list[dict[str, str]] | None = None,
    system_prompt: str | None = None,
    *,
    task: str = "simple_chat",
) -> str:
    """Backward-compatible chat facade routed through the configured LLM profile."""
    from .llm import complete_text

    return complete_text(message, history, system_prompt=system_prompt, task=task)

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
