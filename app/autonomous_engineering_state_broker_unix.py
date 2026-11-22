"""Unix-domain-socket transport for the restricted local state broker."""
from __future__ import annotations

import json
import socket
import time
from typing import Any, Type


_MAX_REQUEST_BYTES = 16 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def request_unix(
    method: str,
    path: str,
    *,
    timeout_ms: int,
    error_type: Type[RuntimeError],
    conflict_type: Type[RuntimeError],
    body: dict[str, Any] | None = None,
) -> Any:
    from .autonomous_engineering_state_broker_config import configured_socket_path

    method = str(method).upper()
    if method not in {"GET", "POST"} or not isinstance(path, str):
        raise ValueError("Unsupported state-broker request.")
    allowed = (
        method == "GET" and path in {"/v1/health", "/v1/pending-work", "/v1/progress"}
    ) or (
        method == "GET" and __import__("re").fullmatch(r"/v1/diagnostics/[0-9]{1,20}", path) is not None
    ) or (
        method == "POST" and path == "/v1/cleanup"
    )
    if not allowed and path in {"/v1/tasks/pending", "/v1/tasks/archived", "/v1/tasks/archive", "/v1/tasks/restore"}:
        from .autonomous_engineering_state_broker_contract import validate_task_request
        validate_task_request(method, path, body)
        allowed = True
    elif body is not None:
        raise ValueError("This state-broker endpoint does not accept a request body.")
    if not allowed:
        raise ValueError("Unsupported state-broker endpoint.")
    request_envelope = {"version": 1, "method": method, "path": path}
    if body is not None:
        request_envelope["body"] = body
    payload = json.dumps(request_envelope, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > _MAX_REQUEST_BYTES:
        raise ValueError("State-broker request is too large.")

    deadline = time.monotonic() + timeout_ms / 1000.0
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise error_type("The local state broker request timed out.")
        client.settimeout(remaining)
        client.connect(str(configured_socket_path()))
        client.sendall(payload)

        response = bytearray()
        while len(response) <= _MAX_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise error_type("The local state broker request timed out.")
            client.settimeout(remaining)
            chunk = client.recv(8192)
            if not chunk:
                break
            newline = chunk.find(b"\n")
            response.extend(chunk[:newline] if newline >= 0 else chunk)
            if len(response) > _MAX_RESPONSE_BYTES:
                raise error_type("The local state broker returned an oversized response.")
            if newline >= 0:
                break
        if not response:
            raise error_type("The local state broker returned an empty response.")
        try:
            envelope = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise error_type("The local state broker returned malformed JSON.") from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("version") != 1
            or isinstance(envelope.get("status"), bool)
            or not isinstance(envelope.get("status"), int)
        ):
            raise error_type("The local state broker returned an invalid response envelope.")
        status = envelope["status"]
        result = envelope.get("payload")
        if status == 409 and isinstance(result, dict):
            blockers = result.get("blockers", [])
            if not isinstance(blockers, list):
                blockers = []
            detail = str(result.get("message") or result.get("error") or "operation_conflict")
            raise conflict_type(detail, [str(item) for item in blockers])
        if status < 200 or status >= 300:
            detail = result.get("error", "request_failed") if isinstance(result, dict) else "request_failed"
            raise error_type(f"State broker rejected the request ({status}: {detail}).")
        return result
    except socket.timeout as exc:
        raise error_type("The local state broker request timed out.") from exc
    except OSError as exc:
        raise error_type(
            f"The local state broker is unavailable ({type(exc).__name__})."
        ) from exc
    finally:
        client.close()
