"""In-process native state transport; no shell, daemon, service or elevation."""
from __future__ import annotations

from typing import Any

from .autonomous_engineering_native_config import validate_native_state_root
from .autonomous_engineering_state import execution_state_directory
from .autonomous_engineering_state_store import BrokerFailure, RestrictedStateBroker


def native_health(*, verify: bool = False) -> dict[str, Any]:
    root = execution_state_directory()
    validate_native_state_root(root)
    if verify:
        RestrictedStateBroker(root).health()
    return {"ok": True, "ready": True, "storage_ready": True, "service": "native", "schema_version": 1}


def setup_native_state() -> dict[str, Any]:
    """Create/verify storage only after an explicit setup action, never on GET."""
    root = execution_state_directory()
    existed = root.exists()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    validate_native_state_root(root)
    # Actual bounded write probe; the same core is used by all native operations.
    result = RestrictedStateBroker(root).health()
    if result.get("ready") is not True:
        raise RuntimeError("Native state storage probe failed.")
    return {"ready": True, "already_ready": existed, "runtime": "native-python",
            "message": "فضای بومی ثبت وضعیت با حساب فعلی آماده است."}


def request_native(method: str, path: str, body: dict[str, Any] | None, *, error_type, conflict_type) -> Any:
    try:
        if method == "GET" and path in {"/v1/health", "/v1/health/verify"}:
            return native_health(verify=path == "/v1/health/verify")
        root = execution_state_directory()
        validate_native_state_root(root)
        store = RestrictedStateBroker(root)
        envelope = {"version": 1, "method": method, "path": path}
        if body is not None:
            envelope["body"] = body
        status, payload = store.dispatch(envelope)
    except BrokerFailure as exc:
        if exc.status == 409:
            raise conflict_type(exc.code, exc.blockers) from exc
        raise error_type(f"Native state rejected request ({exc.status}: {exc.code}).") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise error_type(f"Native state unavailable ({type(exc).__name__}).") from exc
    if status == 409 and isinstance(payload, dict):
        blockers = payload.get("blockers", [])
        raise conflict_type("Native state operation conflicted.", blockers if isinstance(blockers, list) else [])
    if not 200 <= status < 300:
        raise error_type(f"Native state rejected request ({status}).")
    return payload
