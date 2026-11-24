"""Two-phase persistent dispatch reservation through the protected state broker."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from uuid import uuid4

from . import autonomous_engineering_state_broker as state_broker


class DispatchAdmissionError(RuntimeError):
    """The broker returned an invalid or incomplete reservation response."""



@contextmanager
def dispatch_admission_lock() -> Iterator[dict[str, Any]]:
    """Reserve dispatch admission, then commit or release it through the broker.

    The broker owns the protected state and enforces the cross-process grace
    window. An abandoned reservation expires conservatively as an ambiguous
    dispatch, so a process restart cannot silently permit a duplicate run.
    """
    request_id = uuid4().hex
    acquired = state_broker.request(
        "POST",
        "/v1/dispatch-admission/acquire",
        body={"request_id": request_id},
        timeout_ms=3500,
    )
    if not isinstance(acquired, dict) or acquired.get("acquired") is not True:
        raise DispatchAdmissionError("The state broker did not confirm dispatch admission.")
    state: dict[str, Any] = {
        "accepted": False,
        "last_accepted_at": acquired.get("last_accepted_at", 0.0),
    }
    try:
        yield state
    finally:
        completed = state_broker.request(
            "POST",
            "/v1/dispatch-admission/complete",
            body={"request_id": request_id, "accepted": state.get("accepted") is True},
            timeout_ms=3500,
        )
        if not isinstance(completed, dict) or completed.get("completed") is not True:
            raise DispatchAdmissionError("The state broker could not confirm dispatch reservation completion.")
