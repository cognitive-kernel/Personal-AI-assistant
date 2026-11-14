from __future__ import annotations

import json
import urllib.request

import pytest

from scripts.verify_ollama_tools import _TOOL_NAME, verify_tool_calling


class _Response:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int):
        return self.body


class _Opener:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout: int):
        self.requests.append((request, timeout))
        return _Response(self.responses.pop(0))


def _tool_response() -> dict:
    return {
        "message": {
            "tool_calls": [{
                "function": {"name": _TOOL_NAME, "arguments": {}},
            }],
        },
    }


def test_owned_preflight_probe_keeps_model_loaded_and_skips_residency_round_trip(monkeypatch):
    opener = _Opener([_tool_response()])
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: opener)

    assert verify_tool_calling("model:codex", keep_alive="10m") is True

    assert len(opener.requests) == 1
    request, timeout = opener.requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url.endswith("/api/chat")
    assert payload["keep_alive"] == "10m"
    assert timeout == 45


def test_default_probe_preserves_existing_residency_neutral_behavior(monkeypatch):
    opener = _Opener([
        {"models": []},
        _tool_response(),
    ])
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args: opener)

    assert verify_tool_calling("model:latest") is True

    assert len(opener.requests) == 2
    probe_request, _ = opener.requests[1]
    payload = json.loads(probe_request.data.decode("utf-8"))
    assert payload["keep_alive"] == "0"


@pytest.mark.parametrize("value", ["", "forever", "60m", "10"])
def test_probe_rejects_unbounded_or_unsupported_keep_alive(value):
    with pytest.raises(ValueError, match="bounded Ollama durations"):
        verify_tool_calling("model:latest", keep_alive=value)
