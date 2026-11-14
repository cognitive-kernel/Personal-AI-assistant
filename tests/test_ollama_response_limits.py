import io

import pytest

from app import ollama


class _FakeOpener:
    def __init__(self, body: bytes):
        self.body = body

    def open(self, request, timeout=None):
        return io.BytesIO(self.body)


def _install_response(monkeypatch, body: bytes):
    monkeypatch.setattr(ollama, "_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(
        ollama,
        "get_settings",
        lambda: {"ollama_timeout_seconds": 30, "gpu_enabled": False},
    )
    monkeypatch.setattr(
        ollama.urllib.request,
        "build_opener",
        lambda *args, **kwargs: _FakeOpener(body),
    )


def test_show_endpoint_accepts_valid_metadata_larger_than_generic_limit(monkeypatch):
    padding = "x" * (9 * 1024 * 1024)
    body = ollama.json.dumps({"model_info": {}, "padding": padding}).encode("utf-8")
    assert len(body) > ollama._MAX_RESPONSE_BYTES
    assert len(body) <= ollama._MAX_SHOW_RESPONSE_BYTES
    _install_response(monkeypatch, body)

    result = ollama._request("/api/show", {"model": "gpt-oss:20b"}, timeout=8)

    assert result["model_info"] == {}
    assert len(result["padding"]) == len(padding)


def test_non_show_endpoint_keeps_the_eight_mib_response_limit(monkeypatch):
    padding = "x" * (9 * 1024 * 1024)
    body = ollama.json.dumps({"models": [], "padding": padding}).encode("utf-8")
    _install_response(monkeypatch, body)

    with pytest.raises(ollama.OllamaError, match="Ollama response exceeded the 8 MiB limit"):
        ollama._request("/api/tags", timeout=4)
