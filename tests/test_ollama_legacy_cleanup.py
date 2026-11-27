from __future__ import annotations

import pytest

from scripts.ollama_legacy_cleanup import (
    OllamaCleanupError,
    cleanup_legacy_context_variants,
)


class FakeOllama:
    def __init__(
        self, names, *, loaded=(), modelfiles=None, keep_after_delete=(),
        load_on_show=(),
    ):
        self.names = list(names)
        self.loaded = list(loaded)
        self.modelfiles = dict(modelfiles or {})
        self.keep_after_delete = set(keep_after_delete)
        self.load_on_show = set(load_on_show)
        self.calls = []

    def request(self, method, path, body=None, timeout=10.0):
        self.calls.append((method, path, body, timeout))
        if method == "GET" and path == "/api/tags":
            return {"models": [{"name": name} for name in self.names]}
        if method == "GET" and path == "/api/ps":
            return {"models": [{"name": name} for name in self.loaded]}
        if method == "POST" and path == "/api/show":
            name = body["model"]
            if name not in self.names:
                raise OllamaCleanupError("unknown model")
            if name in self.load_on_show and name not in self.loaded:
                self.loaded.append(name)
            return {"modelfile": self.modelfiles.get(name, "")}
        if method == "DELETE" and path == "/api/delete":
            name = body["model"]
            if name not in self.keep_after_delete:
                self.names = [item for item in self.names if item.casefold() != name.casefold()]
            return {"status": "success"}
        raise AssertionError(f"Unexpected Ollama request: {method} {path}")


def test_cleanup_removes_verified_numeric_aliases_across_all_base_models():
    names = [
        "gpt-oss:20b",
        "gpt-oss:20b-codex-ctx",
        "gpt-oss:20b-codex-ctx50760",
        "gpt-oss:20b-codex-ctx55624",
        "gpt-oss:20b-codex-64k",
        "qwen2.5:7b",
        "qwen2.5:7b-codex-ctx2048",
        "qwen2.5:7b-codex-64k",
        "gemma4:12b",
    ]
    api = FakeOllama(
        names,
        loaded=["gpt-oss:20b-codex-ctx55624"],
        modelfiles={
            "gpt-oss:20b-codex-ctx50760": "FROM /blob/base\nPARAMETER num_ctx 50760\n",
            "gpt-oss:20b-codex-ctx55624": "FROM /blob/base\nPARAMETER num_ctx 55624\n",
            "qwen2.5:7b-codex-ctx2048": "FROM /blob/qwen\nPARAMETER num_ctx 2048\n",
        },
    )

    result = cleanup_legacy_context_variants(
        "gpt-oss:20b",
        request_json=api.request,
        audit_only=False,
        sleep=lambda _seconds: None,
    )

    assert result["removed_models"] == [
        "gpt-oss:20b-codex-ctx50760",
        "qwen2.5:7b-codex-ctx2048",
    ]
    assert result["preserved_loaded"] == ["gpt-oss:20b-codex-ctx55624"]
    assert result["removed_count"] == 2
    assert "gpt-oss:20b" in api.names
    assert "gpt-oss:20b-codex-ctx" in api.names
    assert "gpt-oss:20b-codex-64k" in api.names
    assert "qwen2.5:7b" in api.names
    assert "qwen2.5:7b-codex-64k" in api.names
    delete_calls = [call for call in api.calls if call[0] == "DELETE"]
    assert [call[2]["model"] for call in delete_calls] == result["removed_models"]


def test_cleanup_preserves_unverified_or_context_mismatched_numeric_aliases():
    api = FakeOllama(
        [
            "base:7b",
            "base:7b-codex-ctx111773",
            "base:7b-codex-ctx10000",
        ],
        modelfiles={
            "base:7b-codex-ctx111773": "FROM /blob/base\nPARAMETER num_ctx 65536\n",
            "base:7b-codex-ctx10000": "FROM /blob/base\nPARAMETER temperature 0.2\n",
        },
    )

    result = cleanup_legacy_context_variants(
        "base:7b",
        request_json=api.request,
        audit_only=False,
        sleep=lambda _seconds: None,
    )

    assert result["removed_models"] == []
    assert {item["name"] for item in result["preserved_unverified"]} == {
        "base:7b-codex-ctx111773",
        "base:7b-codex-ctx10000",
    }
    assert not [call for call in api.calls if call[0] == "DELETE"]
    assert len(api.names) == 3


def test_cleanup_audit_mode_reports_candidates_without_deleting():
    api = FakeOllama(
        ["base:7b", "base:7b-codex-ctx8192"],
        modelfiles={
            "base:7b-codex-ctx8192": "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
    )

    result = cleanup_legacy_context_variants(
        "base:7b",
        request_json=api.request,
        audit_only=True,
    )

    assert result["would_remove_models"] == ["base:7b-codex-ctx8192"]
    assert result["removed_models"] == []
    assert not [call for call in api.calls if call[0] == "DELETE"]
    assert api.names == ["base:7b", "base:7b-codex-ctx8192"]


def test_cleanup_preserves_current_model_even_if_it_has_a_numeric_suffix():
    api = FakeOllama(
        ["base:7b-codex-ctx8192"],
        modelfiles={
            "base:7b-codex-ctx8192": "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
    )

    result = cleanup_legacy_context_variants(
        "base:7b-codex-ctx8192",
        request_json=api.request,
    )

    assert result["preserved_current"] == ["base:7b-codex-ctx8192"]
    assert result["removed_models"] == []
    assert not [call for call in api.calls if call[0] == "DELETE"]


def test_cleanup_fails_closed_when_loaded_model_inventory_is_unavailable():
    api = FakeOllama(
        ["base:7b-codex-ctx8192"],
        modelfiles={
            "base:7b-codex-ctx8192": "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
    )

    def unavailable(method, path, body=None, timeout=10.0):
        if path == "/api/ps":
            raise OllamaCleanupError("runtime inventory unavailable")
        return api.request(method, path, body, timeout)

    with pytest.raises(OllamaCleanupError, match="runtime inventory unavailable"):
        cleanup_legacy_context_variants("base:7b", request_json=unavailable)

    assert not [call for call in api.calls if call[0] == "DELETE"]


def test_cleanup_fails_if_ollama_still_lists_a_deleted_alias():
    alias = "base:7b-codex-ctx8192"
    api = FakeOllama(
        [alias],
        modelfiles={
            alias: "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
        keep_after_delete=[alias],
    )

    with pytest.raises(OllamaCleanupError, match="still lists legacy tag"):
        cleanup_legacy_context_variants(
            "base:7b",
            request_json=api.request,
            audit_only=False,
            sleep=lambda _seconds: None,
            verify_attempts=2,
        )

    assert [call for call in api.calls if call[0] == "DELETE"]
    assert alias in api.names


def test_cleanup_rechecks_runtime_before_deleting_an_alias_that_becomes_loaded():
    alias = "base:7b-codex-ctx8192"
    api = FakeOllama(
        [alias],
        modelfiles={
            alias: "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
        load_on_show=[alias],
    )

    result = cleanup_legacy_context_variants(
        "base:7b",
        request_json=api.request,
        audit_only=False,
        sleep=lambda _seconds: None,
    )

    assert result["preserved_loaded"] == [alias]
    assert result["removed_models"] == []
    assert not [call for call in api.calls if call[0] == "DELETE"]
    assert alias in api.names


def test_cleanup_defaults_to_audit_only_for_legacy_aliases():
    alias = "base:7b-codex-ctx8192"
    api = FakeOllama(
        ["base:7b", alias],
        modelfiles={
            alias: "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
    )

    result = cleanup_legacy_context_variants(
        "base:7b",
        request_json=api.request,
    )

    assert result["would_remove_models"] == [alias]
    assert result["removed_models"] == []
    assert api.names == ["base:7b", alias]
    assert not [call for call in api.calls if call[0] == "DELETE"]


def test_legacy_cli_requires_explicit_delete_confirmation(monkeypatch, capsys):
    import scripts.ollama_legacy_cleanup as cleanup

    alias = "base:7b-codex-ctx8192"
    api = FakeOllama(
        ["base:7b", alias],
        modelfiles={
            alias: "FROM /blob/base\nPARAMETER num_ctx 8192\n",
        },
    )
    monkeypatch.setattr(cleanup, "_request_json", api.request)

    assert cleanup.main(["--current-model", "base:7b"]) == 0
    assert alias in api.names
    assert not [call for call in api.calls if call[0] == "DELETE"]

    assert cleanup.main([
        "--current-model", "base:7b", "--delete-confirmed-legacy"
    ]) == 0
    assert alias not in api.names
