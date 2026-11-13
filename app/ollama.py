from __future__ import annotations
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from .db import get_settings
from .hardware import detect_hardware, ensure_resource_headroom, model_keep_alive, runtime_limits
from .ollama_context import OllamaModelMetadataError, maximum_context_tokens

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

def normalize_base_url(value: str) -> str:
    """Validate and canonicalize an Ollama HTTP(S) base URL without making a request."""
    if not isinstance(value, str):
        raise OllamaError("Ollama URL must be a string.")
    base = value.rstrip("/")
    try:
        parsed = urllib.parse.urlparse(base)
        hostname = parsed.hostname
    except ValueError as exc:
        # urllib.parse raises for malformed bracketed IPv6 authorities.
        raise OllamaError("Ollama URL must be a valid HTTP(S) URL.") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise OllamaError("Ollama URL must be a valid HTTP(S) URL.")
    if parsed.username is not None or parsed.password is not None:
        raise OllamaError("Credentials in the Ollama URL are not supported.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise OllamaError("Ollama URL contains an invalid port.") from exc
    if parsed.netloc.endswith(":"):
        raise OllamaError("Ollama URL contains an invalid empty port.")
    if parsed.path or parsed.query or parsed.fragment or "?" in base or "#" in base:
        raise OllamaError("Ollama URL must be a base URL without a path, query, or fragment.")
    if port is not None and not 1 <= port <= 65535:
        raise OllamaError("Ollama URL contains an invalid port.")

    hostname = parsed.hostname
    # Scope identifiers are unnecessary for a loopback-only endpoint and make
    # address interpretation platform-dependent; reject them before ipaddress parsing.
    if "%" in hostname:
        raise OllamaError("Scoped IPv6 addresses are not supported for Ollama.")
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


def _base_url() -> str:
    return normalize_base_url(str(get_settings()["ollama_base_url"]))

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent a loopback Ollama endpoint from redirecting requests elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
    *,
    purpose: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if path == "/api/chat" and purpose in {"chat", "learning", "programming", "factual_review", "research_plan"}:
        profile = purpose
        if settings.get(f"{profile}_llm_provider", "ollama") == "openai_compatible":
            request_payload = payload if isinstance(payload, dict) else {}
            messages = request_payload.get("messages", [])
            if not isinstance(messages, list) or not messages:
                raise OllamaError("OpenAI-compatible chat requires a non-empty messages list.")
            system_prompt = None
            history = []
            user_message = None
            for item in messages:
                if not isinstance(item, dict) or item.get("role") not in {"system", "user", "assistant"}:
                    continue
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                if item["role"] == "system" and system_prompt is None:
                    system_prompt = content
                elif item["role"] == "user":
                    if user_message is not None:
                        history.append({"role": "user", "content": user_message})
                    user_message = content
                elif item["role"] == "assistant":
                    history.append({"role": "assistant", "content": content})
            if not isinstance(user_message, str):
                raise OllamaError("OpenAI-compatible chat requires a user message.")
            options = request_payload.get("options", {})
            options = options if isinstance(options, dict) else {}
            content = _openai_compatible_chat(
                user_message, history, system_prompt,
                purpose=profile,
                model=str(request_payload.get("model") or settings.get(f"{profile}_ollama_model", "")).strip(),
                timeout=int(timeout or settings.get(f"{profile}_ollama_timeout_seconds", 120)),
                num_predict=options.get("num_predict"),
                response_format=request_payload.get("format"),
                usage_out=(usage := {}),
            )
            return {"message": {"content": content}, "provider": "openai_compatible", **usage}
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
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
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


def _model_context_metadata(model_info: dict[str, Any], architecture: str | None = None) -> int | None:
    try:
        return maximum_context_tokens(model_info, architecture)
    except OllamaModelMetadataError:
        return None

def _dedicated_gpu_budget_gb(hardware: dict[str, Any]) -> float | None:
    """Return measured dedicated VRAM; shared integrated-GPU RAM is not separate memory."""
    devices = hardware.get("gpu_devices")
    if not isinstance(devices, list) or len(devices) != 1:
        return None
    device = devices[0]
    if not isinstance(device, dict):
        return None
    device_type = str(device.get("device_type") or "").casefold()
    vendor = str(device.get("vendor") or "").casefold()
    name = str(device.get("name") or "").casefold()
    if "integrated" in device_type or (
        vendor == "intel" and any(token in name for token in ("uhd", "iris", "integrated"))
    ):
        return None
    free_mb = device.get("memory_free_mb")
    if (
        isinstance(free_mb, bool)
        or not isinstance(free_mb, (int, float))
        or not math.isfinite(free_mb)
        or free_mb <= 0
    ):
        return None
    return float(free_mb) / 1024


def _model_memory_estimate(
    model: str,
    model_info: dict[str, Any],
    details: dict[str, Any],
    weight_bytes: int | None,
    context_tokens: int | None = None,
) -> dict[str, Any] | None:
    """Build a conservative model-memory estimate from Ollama's measured metadata."""
    architecture = str(details.get("family") or "").strip()

    def metadata_number(suffix: str) -> int | None:
        preferred = f"{architecture}{suffix}" if architecture else None
        if preferred:
            value = model_info.get(preferred)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
            return None
        values = []
        for key, value in model_info.items():
            if not str(key).endswith(suffix):
                continue
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                values.append(value)
        # Without a family, accept only an unambiguous metadata dimension.
        return values[0] if values and len(set(values)) == 1 else None

    layers = metadata_number(".block_count")
    kv_heads = metadata_number(".attention.head_count_kv")
    if kv_heads is None:
        # Full attention heads are a conservative upper bound when KV-head count
        # is not published; do not infer a smaller grouped-query count.
        kv_heads = metadata_number(".attention.head_count")
    key_length = metadata_number(".attention.key_length")
    value_length = metadata_number(".attention.value_length")
    if not all((layers, kv_heads, key_length, value_length)):
        return None

    if isinstance(weight_bytes, bool) or not isinstance(weight_bytes, int) or weight_bytes <= 0:
        return None

    weights_gib = weight_bytes / (1024 ** 3)
    dimensions_per_token = layers * kv_heads * (key_length + value_length)
    # KV-cache dtype is not exposed consistently by Ollama. Budget at 4 bytes
    # per element (FP32 upper bound) rather than assuming the smaller FP16 cache.
    cache_bytes_upper_bound = dimensions_per_token * 4
    cache_bytes_fp16 = dimensions_per_token * 2
    estimate: dict[str, Any] = {
        "weight_size_bytes": weight_bytes,
        "weights_gib": weights_gib,
        "overhead_gib": max(0.5, weights_gib * 0.10),
        "kv_cache_bytes_per_token": cache_bytes_upper_bound,
        "kv_cache_bytes_per_token_upper_bound": cache_bytes_upper_bound,
        "kv_cache_bytes_per_token_fp16": cache_bytes_fp16,
        "kv_cache_dtype": "unknown; FP32 upper bound used for safe budgeting",
    }
    effective_context = (
        context_tokens
        if isinstance(context_tokens, int) and not isinstance(context_tokens, bool) and context_tokens > 0
        else None
    )
    estimate["context_tokens"] = effective_context
    if effective_context is not None:
        cache_upper_gib = cache_bytes_upper_bound * effective_context / (1024 ** 3)
        cache_fp16_gib = cache_bytes_fp16 * effective_context / (1024 ** 3)
        estimate["kv_cache_gib_upper_bound"] = cache_upper_gib
        estimate["total_ram_gib_upper_bound"] = weights_gib + cache_upper_gib + estimate["overhead_gib"]
        # Keep the existing FP16 display fields for clients that still consume them.
        estimate["kv_cache_gib_float16"] = cache_fp16_gib
        estimate["total_ram_gib_float16"] = weights_gib + cache_fp16_gib + estimate["overhead_gib"]
        estimate["full_gpu_vram_gib_float16"] = estimate["total_ram_gib_float16"]
    return estimate


def _recommended_context_tokens(
    maximum_context: int | None,
    memory_estimate: dict[str, Any] | None,
    hardware: dict[str, Any],
) -> int | None:
    """Return a context only when model dimensions and current memory are measurable."""
    if not isinstance(maximum_context, int) or isinstance(maximum_context, bool) or maximum_context < 512:
        return None
    from .hardware import dynamic_ram_budget_gb
    budget = dynamic_ram_budget_gb(get_settings(), hardware)
    if budget is None or not isinstance(memory_estimate, dict):
        return None
    weights = memory_estimate.get("weights_gib")
    overhead = memory_estimate.get("overhead_gib")
    cache_per_token = memory_estimate.get("kv_cache_bytes_per_token_upper_bound")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
        for value in (weights, overhead, cache_per_token)
    ):
        return None
    if get_settings().get("gpu_enabled") is True:
        gpu_budget = _dedicated_gpu_budget_gb(hardware)
        # Shared iGPU memory is already part of host RAM; never count it twice.
        if gpu_budget is not None and gpu_budget > float(weights) + float(overhead):
            budget = gpu_budget
    available_for_cache = budget - float(weights) - float(overhead)
    if not math.isfinite(available_for_cache) or available_for_cache <= 0:
        return None
    fit = int(available_for_cache * (1024 ** 3) / float(cache_per_token))
    candidates = (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576)
    valid = [value for value in candidates if value <= maximum_context and value <= fit]
    return max(valid) if valid else None



def model_identity(
    purpose: str, model: str, *, settings: dict[str, Any] | None = None
) -> dict[str, str]:
    """Return a stable identity for a configured model without claiming remote weight attestation."""
    settings = settings if isinstance(settings, dict) else get_settings()
    provider = settings.get(f"{purpose}_llm_provider", "ollama")
    if provider == "ollama":
        entry = next(
            (item for item in list_models()
             if isinstance(item, dict) and str(item.get("name", "")).strip() == model),
            None,
        )
        digest = entry.get("digest") if entry else None
        if not isinstance(digest, str) or not digest.strip():
            raise OllamaError("The configured Ollama model is not installed or does not expose a digest.")
        return {"provider": provider, "model": model, "identity": digest, "identity_kind": "ollama_model_digest"}
    if provider == "openai_compatible":
        base_url = normalize_openai_compatible_base_url(
            settings.get(f"{purpose}_llm_base_url", "")
        )
        fingerprint = hashlib.sha256(
            f"{provider}|{base_url}|{model}".encode("utf-8")
        ).hexdigest()
        return {
            "provider": provider,
            "model": model,
            "identity": fingerprint,
            "identity_kind": "endpoint_model_configuration_fingerprint",
        }
    raise OllamaError(f"Unsupported {purpose} model identity provider.")


def model_details(model: str, context_tokens: int | None = None) -> dict[str, Any]:
    """Return local Ollama model metadata and a conservative memory/context estimate."""
    name = str(model or "").strip()
    if not name or len(name) > 200:
        raise OllamaError("Model name is invalid.")
    info = _request("/api/show", {"model": name}, timeout=8)
    model_info = info.get("model_info") if isinstance(info.get("model_info"), dict) else {}
    details = info.get("details") if isinstance(info.get("details"), dict) else {}
    maximum_context = _model_context_metadata(model_info, details.get("family"))
    tag = next((item for item in list_models() if item.get("name") == name), None)
    weight_bytes = tag.get("size") if isinstance(tag, dict) else None
    estimate = _model_memory_estimate(
        name,
        model_info,
        details,
        weight_bytes,
        context_tokens if context_tokens is not None else maximum_context,
    )
    running = _request("/api/ps", timeout=4).get("models", [])
    running_item = next((item for item in running if isinstance(item, dict) and item.get("name") == name), None) if isinstance(running, list) else None
    hardware = detect_hardware()
    recommended_context = _recommended_context_tokens(maximum_context, estimate, hardware)
    raw_capabilities = info.get("capabilities")
    capabilities = sorted({
        value for value in raw_capabilities if isinstance(value, str) and value
    }) if isinstance(raw_capabilities, list) else []
    return {
        "name": name,
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level"),
        "maximum_context_tokens": maximum_context,
        "recommended_context_tokens": recommended_context,
        "context_metadata_available": maximum_context is not None,
        "weight_size_bytes": weight_bytes,
        "architecture": details.get("family"),
        "capabilities": capabilities,
        "memory_estimate": estimate,
        "memory_estimate_note": (
            "Safe planning uses an FP32 upper bound for KV cache because Ollama does not consistently expose the runtime cache dtype. "
            "A hardware-safe recommendation is omitted when model dimensions, weights, or available RAM are unknown."
        ),
        "running": {
            "context_length": running_item.get("context_length"),
            "size_bytes": running_item.get("size"),
            "size_vram_bytes": running_item.get("size_vram"),
            "processor": ("GPU" if isinstance(running_item.get("size_vram"), int) and running_item.get("size_vram") > 0 else "CPU" if isinstance(running_item.get("size_vram"), int) else None),
        } if running_item else None,
    }



def runtime_diagnostics(
    model: str | None = None,
    context_tokens: int | None = None,
    purpose: str = "chat",
) -> dict[str, Any]:
    """Combine host telemetry with Ollama's observed loaded-model allocation."""
    hardware = detect_hardware()
    settings = get_settings()
    profile = purpose if purpose in {"chat", "learning", "programming"} else "chat"
    requested_context = context_tokens if isinstance(context_tokens, int) and not isinstance(context_tokens, bool) else settings.get(f"{profile}_ollama_num_ctx", settings.get("ollama_num_ctx", 2048))
    result: dict[str, Any] = {
        "hardware": hardware,
        "ollama_running_models": [],
        "runtime_limits": runtime_limits({
            "ollama_num_ctx": requested_context,
            "ollama_num_thread": settings.get(f"{profile}_ollama_num_thread", settings.get("ollama_num_thread", 0)),
            "cpu_resource_limit_percent": settings.get("cpu_resource_limit_percent", 90),
            "ram_budget_max_gb": settings.get("ram_budget_max_gb", 25),
            "ram_safety_reserve_gb": settings.get("ram_safety_reserve_gb", 4),
        }, hardware),
    }
    if settings.get(f"{profile}_llm_provider", "ollama") == "openai_compatible":
        result["provider"] = "openai_compatible"
        result["runtime_limits"] = {"num_ctx": requested_context, "cpu_threads": 0}
        if model:
            result["selected_model"] = {
                "name": model,
                "maximum_context_tokens": None,
                "recommended_context_tokens": None,
                "memory_estimate": None,
                "provider_metadata_available": False,
            }
        return result
    result["provider"] = "ollama"
    try:
        running = _request("/api/ps", timeout=4).get("models", [])
        if isinstance(running, list):
            result["ollama_running_models"] = [
                {
                    "name": item.get("name"),
                    "context_length": item.get("context_length"),
                    "size_bytes": item.get("size"),
                    "size_vram_bytes": item.get("size_vram"),
                    "processor": ("GPU" if isinstance(item.get("size_vram"), int) and item.get("size_vram") > 0 else "CPU" if isinstance(item.get("size_vram"), int) else None),
                }
                for item in running if isinstance(item, dict)
            ]
    except OllamaError as exc:
        result["ollama_ps_error"] = str(exc)
    if model:
        try:
            result["selected_model"] = model_details(model, context_tokens)
            maximum = result["selected_model"].get("maximum_context_tokens")
            result["runtime_limits"] = runtime_limits({
                "ollama_num_ctx": requested_context,
                "ollama_num_thread": settings.get(f"{profile}_ollama_num_thread", settings.get("ollama_num_thread", 0)),
                "cpu_resource_limit_percent": settings.get("cpu_resource_limit_percent", 90),
                "ram_budget_max_gb": settings.get("ram_budget_max_gb", 25),
                "ram_safety_reserve_gb": settings.get("ram_safety_reserve_gb", 4),
            }, hardware, maximum_context_tokens=maximum if isinstance(maximum, int) else None)
        except OllamaError as exc:
            result["selected_model_error"] = str(exc)
    return result


def normalize_openai_compatible_base_url(value: str) -> str:
    """Validate an OpenAI-compatible base URL; plaintext HTTP is restricted to loopback."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise OllamaError("OpenAI-compatible base URL must be a non-empty URL of at most 2048 characters.")
    raw = value.strip().rstrip("/")
    try:
        parsed = urllib.parse.urlparse(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise OllamaError("OpenAI-compatible base URL is malformed.") from exc
    if (
        parsed.scheme not in {"https", "http"} or not hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or "?" in raw or "#" in raw
        or parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535)
        or len(parsed.path) > 512
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise OllamaError("OpenAI-compatible base URL is invalid.")
    if parsed.scheme != "https":
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            raise OllamaError("Non-local OpenAI-compatible endpoints must use HTTPS.")
    if hostname.lower() == "localhost":
        netloc = "127.0.0.1" if port is None else f"127.0.0.1:{port}"
        parsed = parsed._replace(netloc=netloc)
        raw = urllib.parse.urlunparse(parsed)
    return raw


def _openai_compatible_chat(
    message: str,
    history: list[dict[str, str]] | None,
    system_prompt: str | None,
    *,
    purpose: str,
    model: str,
    timeout: int,
    num_predict: int | None,
    response_format: str | None = None,
    usage_out: dict[str, int] | None = None,
) -> str:
    """Call an OpenAI-compatible endpoint, including local LM Studio/vLLM/llama.cpp servers."""
    settings = get_settings()
    base_url = settings.get(f"{purpose}_llm_base_url", "")
    normalized_base = normalize_openai_compatible_base_url(base_url)
    parsed = urllib.parse.urlparse(normalized_base)
    api_key_env = settings.get(f"{purpose}_llm_api_key_env", "")
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", api_key_env):
            raise OllamaError("The API-key environment variable name is invalid.")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise OllamaError(f"The configured API-key environment variable '{api_key_env}' is not set.")
        if len(api_key) > 4096 or any(ord(character) < 32 for character in api_key):
            raise OllamaError("The configured API key contains invalid header characters or exceeds 4096 characters.")
        headers["Authorization"] = f"Bearer {api_key}"
    messages = []
    if isinstance(system_prompt, str) and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()[:6000]})
    for item in history or []:
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
            messages.append({"role": item["role"], "content": item["content"][:12000]})
    message_limit = (
        256000 if purpose == "learning"
        else 72000 if purpose == "programming"
        else 16000 if purpose == "factual_review"
        else 12000
    )
    messages.append({"role": "user", "content": message[:message_limit]})
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if num_predict is not None:
        if isinstance(num_predict, bool) or not isinstance(num_predict, int) or not 1 <= num_predict <= 8192:
            raise ValueError("num_predict must be an integer between 1 and 8192.")
        payload["max_tokens"] = num_predict
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}
    if purpose == "factual_review":
        payload["max_tokens"] = 500
    elif purpose == "research_plan":
        payload["max_tokens"] = 700
    request = urllib.request.Request(
        normalized_base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        acquired = _INFERENCE_LOCK.acquire(timeout=max(1.0, min(float(timeout), 600.0)))
        if not acquired:
            raise OllamaError("Timed out waiting for the shared LLM inference slot.", retryable=True)
        try:
            if parsed.scheme == "http":
                _ensure_resource_headroom()
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirectHandler()
            )
            with opener.open(request, timeout=max(5, min(int(timeout), 600))) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
        finally:
            _INFERENCE_LOCK.release()
        if len(body) > _MAX_RESPONSE_BYTES:
            raise OllamaError("LLM response exceeded the 8 MiB limit.")
        result = json.loads(body.decode("utf-8"))
        usage = result.get("usage") if isinstance(result, dict) else None
        if usage_out is not None and isinstance(usage, dict):
            for source_key, target_key in (
                ("prompt_tokens", "prompt_eval_count"),
                ("completion_tokens", "eval_count"),
                ("total_tokens", "total_tokens"),
            ):
                value = usage.get(source_key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage_out[target_key] = value
        choices = result.get("choices") if isinstance(result, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        content = choice.get("message", {}).get("content") if isinstance(choice, dict) else None
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("The selected LLM returned no text content.")
        return content.strip()
    except urllib.error.HTTPError as exc:
        raise OllamaError(
            f"OpenAI-compatible provider returned HTTP {exc.code}.",
            retryable=exc.code in {408, 425, 429, 500, 502, 503, 504},
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OllamaError(
            f"OpenAI-compatible request failed: {type(exc).__name__}", retryable=True
        ) from exc


def chat(
    message: str,
    history: list[dict[str, str]] | None = None,
    system_prompt: str | None = None,
    *,
    purpose: str = "chat",
    model_override: str | None = None,
    num_predict: int | None = None,
    num_ctx_override: int | None = None,
) -> str:
    if purpose not in {"chat", "learning", "programming", "factual_review", "research_plan"}:
        raise ValueError("purpose must be chat, learning, programming, factual_review, or research_plan.")
    settings = get_settings()
    prefix = purpose
    provider_profile = purpose
    hardware = detect_hardware()
    request_timeout = int(settings.get(f"{prefix}_ollama_timeout_seconds", settings.get("ollama_timeout_seconds", 120)))
    if purpose in {"factual_review", "research_plan"}:
        request_timeout = max(1, min(request_timeout, 45))
    model = str(model_override if model_override is not None else settings.get(f"{prefix}_ollama_model", settings.get("ollama_model", "qwen2.5:7b"))).strip()
    if not model or len(model) > 200:
        raise ValueError("model must be a non-empty name of at most 200 characters.")
    provider = settings.get(f"{provider_profile}_llm_provider", "ollama")
    if provider == "openai_compatible":
        return _openai_compatible_chat(
            message, history, system_prompt, purpose=provider_profile, model=model,
            timeout=request_timeout, num_predict=num_predict,
        )
    if provider != "ollama":
        raise OllamaError(f"Unsupported {purpose} LLM provider: {provider}.")
    model_details_payload = _request("/api/show", {"model": model}, timeout=8)
    model_info = model_details_payload.get("model_info", {})
    model_details_meta = model_details_payload.get("details", {})
    architecture = model_details_meta.get("family") if isinstance(model_details_meta, dict) else None
    maximum_context = _model_context_metadata(model_info if isinstance(model_info, dict) else {}, architecture)
    configured_context = settings.get(f"{prefix}_ollama_num_ctx", settings.get("ollama_num_ctx", 4096))
    if num_ctx_override is not None:
        if (
            maximum_context is None
            or isinstance(num_ctx_override, bool)
            or not isinstance(num_ctx_override, int)
            or not 512 <= num_ctx_override <= maximum_context
        ):
            raise OllamaError("The selected model cannot provide the required learning context length.")
        configured_context = max(int(configured_context), num_ctx_override)
    runtime_settings = {
        "ollama_num_ctx": configured_context,
        "ollama_num_thread": settings.get(f"{prefix}_ollama_num_thread", settings.get("ollama_num_thread", 0)),
        "cpu_resource_limit_percent": settings.get("cpu_resource_limit_percent", 90),
        "ram_budget_max_gb": settings.get("ram_budget_max_gb", 25),
        "ram_safety_reserve_gb": settings.get("ram_safety_reserve_gb", 4),
    }
    if maximum_context is None:
        raise OllamaError(
            f"Ollama did not publish a verifiable context_length for '{model}'; "
            "refusing to send a request with an unverified context limit."
        )
    tag = next((item for item in list_models() if item.get("name") == model), None)
    weight_bytes = tag.get("size") if isinstance(tag, dict) else None
    memory_estimate = _model_memory_estimate(
        model,
        model_info if isinstance(model_info, dict) else {},
        model_details_meta if isinstance(model_details_meta, dict) else {},
        weight_bytes,
    )
    hardware_safe_context = _recommended_context_tokens(maximum_context, memory_estimate, hardware)
    if hardware_safe_context is None:
        raise OllamaError(
            f"Cannot calculate a hardware-safe context for '{model}': verified model weights, "
            "KV-cache dimensions, or available-memory telemetry are missing or insufficient. "
            "No inference request was sent."
        )
    limits = runtime_limits(
        runtime_settings,
        hardware,
        maximum_context_tokens=maximum_context,
    )
    # Enforce all three ceilings at dispatch: configured request, model metadata,
    # and current measured host/GPU memory. Integrated shared RAM is never VRAM.
    limits["num_ctx"] = min(limits["num_ctx"], maximum_context, hardware_safe_context)
    if num_ctx_override is not None and limits["num_ctx"] < num_ctx_override:
        raise OllamaError(
            "The current safe RAM budget cannot provide the context required for this learning generation; "
            "the request was stopped rather than producing a truncated outline."
        )
    keep_alive = model_keep_alive(purpose, hardware, settings)
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
    if not bool(settings.get("gpu_enabled", False)):
        options["num_gpu"] = 0
    if num_predict is not None:
        if isinstance(num_predict, bool) or not isinstance(num_predict, int) or not 1 <= num_predict <= 8192:
            raise ValueError("num_predict must be an integer between 1 and 8192.")
        options["num_predict"] = num_predict
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
    from . import usage_metrics
    usage_metrics.record_ollama_response(purpose=purpose, model=model, response=response)
    return result



def _openai_compatible_embeddings(
    texts: list[str], *, model: str, timeout: int
) -> list[list[float]]:
    settings = get_settings()
    base_url = normalize_openai_compatible_base_url(
        settings.get("embedding_llm_base_url", "")
    )
    api_key_env = settings.get("embedding_llm_api_key_env", "")
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", api_key_env):
            raise OllamaError("The embedding API-key environment variable name is invalid.")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise OllamaError(f"The configured embedding API-key environment variable '{api_key_env}' is not set.")
        if len(api_key) > 4096 or any(ord(character) < 32 for character in api_key):
            raise OllamaError("The embedding API key contains invalid header characters or exceeds 4096 characters.")
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        base_url + "/embeddings",
        data=json.dumps({"model": model, "input": texts}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        acquired = _INFERENCE_LOCK.acquire(timeout=max(1.0, min(float(timeout), 600.0)))
        if not acquired:
            raise OllamaError("Timed out waiting for the shared embedding inference slot.", retryable=True)
        try:
            if urllib.parse.urlparse(base_url).scheme == "http":
                _ensure_resource_headroom()
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirectHandler()
            )
            with opener.open(request, timeout=max(1, min(int(timeout), 600))) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
        finally:
            _INFERENCE_LOCK.release()
        if len(body) > _MAX_RESPONSE_BYTES:
            raise OllamaError("Embedding response exceeded the 8 MiB limit.")
        payload = json.loads(body.decode("utf-8"))
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise OllamaError("OpenAI-compatible provider returned an unexpected embedding count.")
        ordered: list[list[float] | None] = [None] * len(texts)
        for row in rows:
            if not isinstance(row, dict):
                raise OllamaError("OpenAI-compatible provider returned an invalid embedding item.")
            index = row.get("index")
            vector = row.get("embedding")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(texts):
                raise OllamaError("OpenAI-compatible provider returned an invalid embedding index.")
            if ordered[index] is not None or not isinstance(vector, list):
                raise OllamaError("OpenAI-compatible provider returned duplicate or invalid embedding data.")
            ordered[index] = vector
        if any(vector is None for vector in ordered):
            raise OllamaError("OpenAI-compatible provider omitted an embedding.")
        return [vector for vector in ordered if vector is not None]
    except urllib.error.HTTPError as exc:
        raise OllamaError(
            f"OpenAI-compatible embedding provider returned HTTP {exc.code}.",
            retryable=exc.code in {408, 425, 429, 500, 502, 503, 504},
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OllamaError(
            f"OpenAI-compatible embedding request failed: {type(exc).__name__}", retryable=True
        ) from exc


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
    if settings.get("embedding_llm_provider", "ollama") == "openai_compatible":
        embeddings = _openai_compatible_embeddings(
            normalized, model=selected_model, timeout=configured_timeout
        )
    elif settings.get("embedding_llm_provider", "ollama") == "ollama":
        response = _request(
            "/api/embed",
            {"model": selected_model, "input": normalized},
            timeout=max(1, min(configured_timeout, 8)),
        )
        embeddings = response.get("embeddings")
    else:
        raise OllamaError("Unsupported embedding provider.")
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
